#!/usr/bin/env python3
"""test_mcp.py — `container-map mcp` (docs/MCP.md): container_map_mcp.py run as a real
subprocess and spoken to over its stdin and stdout, against a real bridge on a free loopback port
(the rig of test_workflows_api.py, fake agents only), and with no map at all. Initialize and its
version negotiation, batches, the tools and their schemas, each tool's call, a bridge refusal
returned as a tool result with the bridge's own words, malformed input, the live file read again
after a restart, and the token never in anything the server prints. Temp dirs only; never a live
map."""
import io
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testenv  # noqa: E402,F401  (first: clears what steers the tool)
import bridge  # noqa: E402
import cli  # noqa: E402
import container_map_mcp as mcp  # noqa: E402
import test_workflows_api as api  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MCP = os.path.join(HERE, "container_map_mcp.py")
CLI = os.path.join(HERE, "cli.py")
TOOLS = {"map", "unit", "threads", "thread", "thread_start", "thread_followup", "thread_check",
         "show", "show_clear", "page", "focus"}
KEYS = {"type", "properties", "required", "additionalProperties", "description", "enum", "items",
        "pattern", "minimum", "maximum", "minLength", "maxLength", "maxItems"}
NOBODY = "20261001-000000-nope-abcdef"      # a thread id that is not there


class Client:
    """One server process: send a message, read the next line of its stdout, keep everything it
    wrote to stdout and stderr."""

    def __init__(self, argv, env, cwd):
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=env, cwd=cwd)
        self.lines = queue.Queue()
        self.stdout, self.stderr = [], []
        self.n = 0
        streams = ((self.proc.stdout, self.stdout), (self.proc.stderr, self.stderr))
        self.pumps = [threading.Thread(target=self._pump, args=pair, daemon=True)
                      for pair in streams]
        for pump in self.pumps:
            pump.start()

    def _pump(self, stream, keep):
        for line in stream:
            keep.append(line)
            if keep is self.stdout:
                self.lines.put(line)

    def send_raw(self, data):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, msg):
        self.send_raw((json.dumps(msg) + "\n").encode("utf-8"))

    def recv(self, timeout=30):
        return json.loads(self.lines.get(timeout=timeout))

    def request(self, method, params=None):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)
        got = self.recv()
        assert got.get("id") == self.n, got
        return got

    def tool(self, name, arguments=None):
        got = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        assert "error" not in got, got
        return got["result"]

    def initialize(self, version="2025-06-18"):
        return self.request("initialize", {"protocolVersion": version, "capabilities": {},
                                           "clientInfo": {"name": "test-agent", "version": "1"}})

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        for pump in self.pumps:
            pump.join(5)
        for stream in (self.proc.stdout, self.proc.stderr):
            stream.close()
        return (b"".join(self.stdout).decode("utf-8", "replace"),
                b"".join(self.stderr).decode("utf-8", "replace"))


class Session(api.Rig):
    """A bridge, and a server pointed at its live file."""

    def start(self, argv=None, env=None):
        client = Client(argv or [sys.executable, MCP], env or self.env(), self.root)
        self.addCleanup(client.close)
        return client

    def text(self, out):
        """The JSON a result says in its text, which is its structuredContent too."""
        self.assertEqual(out["content"][0]["type"], "text")
        value = json.loads(out["content"][0]["text"])
        if "structuredContent" in out:
            self.assertEqual(value, out["structuredContent"])
        return value

    def good(self, out):
        self.assertNotIn("isError", out, out)
        return self.text(out)

    def bad(self, out):
        self.assertIs(out.get("isError"), True, out)
        return out["content"][0]["text"]

    def wait_thread(self, client, tid, states=("review", "failed", "merged")):
        end = time.monotonic() + 30
        while time.monotonic() < end:
            got = self.good(client.tool("thread", {"id": tid}))
            if got["status"] in states:
                return got
            time.sleep(0.1)
        self.fail("the thread never settled")


class Protocol(Session):
    def test_initialize_answers_the_clients_version_if_we_speak_it_else_our_newest(self):
        for version in ("2025-06-18", "2025-03-26", "2024-11-05"):
            client = self.start()
            got = client.initialize(version)["result"]
            self.assertEqual(got["protocolVersion"], version)
        client = self.start()
        got = client.initialize("2031-01-01")["result"]
        self.assertEqual(got["protocolVersion"], "2025-06-18")
        self.assertEqual(got["capabilities"], {"tools": {}})
        self.assertEqual(got["serverInfo"], {"name": "container-map", "version": cli.VERSION})
        said = got["instructions"]
        self.assertIn("map", said)
        for word in ("merge", "close", "person"):
            self.assertIn(word, said, "what stays with the person")
        self.assertLess(len(said), 1500, "short instructions")
        self.assertEqual(client.request("initialize", {})["error"]["code"], -32602)
        self.assertEqual(client.request("ping"), {"jsonrpc": "2.0", "id": client.n, "result": {}})

    def test_every_tool_has_a_valid_schema_and_states_its_limits(self):
        client = self.start()
        client.initialize()
        tools = client.request("tools/list")["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, TOOLS)
        for tool in tools:
            name, schema = tool["name"], tool["inputSchema"]
            self.assertRegex(name, r"^[a-z][a-z0-9_]*$")
            self.assertTrue(tool["description"].strip() and tool["title"].strip(), name)
            self.assertEqual(schema["type"], "object", name)
            self.assertIs(schema["additionalProperties"], False, name)
            props = schema["properties"]
            self.assertIsInstance(props, dict, name)
            self.assertLessEqual(set(schema.get("required", [])), set(props), name)
            self.assertNotEqual(schema.get("required"), [], f"{name}: no empty required list")
            self.assertLessEqual(set(schema) - {"type", "properties", "required",
                                                "additionalProperties"}, set(), name)
            for key, prop in props.items():
                self.assertLessEqual(set(prop), KEYS, f"{name}.{key}")
                self.assertIn(prop["type"], ("string", "integer", "boolean", "array", "object"))
                self.assertTrue(prop["description"].strip(), f"{name}.{key}")
                if "minimum" in prop and "maximum" in prop:
                    self.assertLessEqual(prop["minimum"], prop["maximum"])
                if "pattern" in prop:
                    re.compile(prop["pattern"])
            hints = tool["annotations"]
            self.assertIs(hints["destructiveHint"], False, name)
            self.assertIs(hints["readOnlyHint"], name in ("map", "unit", "threads", "thread"), name)
            json.dumps(tool)
        by = {t["name"]: t["inputSchema"] for t in tools}
        # the arguments are the routes' own bodies (docs/AGENT-API.md)
        keys = {name: set(schema["properties"]) for name, schema in by.items()}
        self.assertEqual(keys["thread_start"], {"text", "target", "runner", "isolate", "model",
                                                "verify", "retries", "setup"})
        self.assertEqual(by["thread_start"]["required"], ["text", "target", "runner"])
        self.assertEqual(keys["thread_followup"], {"id", "text"})
        self.assertEqual(keys["thread_check"], {"id", "runner", "model", "text"})
        self.assertEqual(keys["thread"], {"id", "run", "since"})
        self.assertEqual(keys["show"], {"ops", "at", "by", "ttl", "id"})
        self.assertEqual(keys["page"], {"title", "html", "at", "by", "ttl", "id"})
        self.assertEqual(by["page"]["required"], ["title", "html"])
        self.assertEqual(keys["focus"], {"uid", "by"})
        ops = by["show"]["properties"]["ops"]["items"]["properties"]["op"]["enum"]
        self.assertEqual(set(ops), set(bridge.SHOW_OPS))
        # the limits are in the words the agent reads, taken from the bridge's own bounds
        say = {t["name"]: t["description"] for t in tools}
        for needle in ("262144", "4 pages", "40 characters", "inline", "data:", "24 hours", "1800",
                       "network"):
            self.assertIn(needle, say["page"], needle)
        for needle in ("16", "900", "300", "skipped", "clamped", "8 are live"):
            self.assertIn(needle, say["show"], needle)
        for needle in ("8000", "agent_verify", "4 running"):
            self.assertIn(needle, say["thread_start"], needle)
        for name in ("thread_start", "thread_followup", "thread_check"):
            self.assertRegex(say[name], r"merge, close, set aside, stop\) is NOT available", name)
        self.assertIn("setting", say["focus"])

    def test_malformed_input_unknown_methods_and_notifications(self):
        client = self.start()
        client.send_raw(b"{not json\n")
        got = client.recv()
        self.assertEqual((got["id"], got["error"]["code"]), (None, -32700))
        client.send_raw(b"\xff\xfe\n")
        self.assertEqual(client.recv()["error"]["code"], -32700, "bytes that are not UTF-8")
        client.send_raw(b"[1, 2]\n")
        self.assertEqual(client.recv()["error"]["code"], -32600, "a batch")
        client.send_raw(b'"hello"\n')
        self.assertEqual(client.recv()["error"]["code"], -32600)
        client.send({"jsonrpc": "2.0", "id": 7})
        got = client.recv()
        self.assertEqual((got["id"], got["error"]["code"]), (7, -32600), "no method")
        client.send({"jsonrpc": "1.0", "id": 8, "method": "ping"})
        self.assertEqual(client.recv()["error"]["code"], -32600)
        client.send({"jsonrpc": "2.0", "id": True, "method": "ping"})
        self.assertEqual(client.recv()["error"]["code"], -32600, "an id is a number or a string")
        client.send({"jsonrpc": "2.0", "id": 9, "method": "ping", "params": 5})
        self.assertEqual(client.recv()["error"]["code"], -32600)
        client.send({"jsonrpc": "2.0", "id": "abc", "method": "no/such"})
        got = client.recv()
        self.assertEqual((got["id"], got["error"]["code"]), ("abc", -32601))
        self.assertEqual(client.request("resources/list")["error"]["code"], -32601)
        self.assertEqual(client.request("prompts/list")["error"]["code"], -32601)
        # notifications get no reply, whatever they are: the next line on stdout answers the
        # ping that came after them
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        client.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                     "params": {"requestId": 1}})
        client.send({"jsonrpc": "2.0", "method": "no/such"})
        client.send({"jsonrpc": "2.0", "id": 99, "result": {}})     # an answer to nothing it asked
        client.send_raw(b"\n   \n")                                  # blank lines are nothing
        client.send({"jsonrpc": "2.0", "id": 100, "method": "ping"})
        self.assertEqual(client.recv(), {"jsonrpc": "2.0", "id": 100, "result": {}})
        # tools/call: an unknown tool and a call with no name are protocol errors; a bad
        # argument is a tool result the agent can read and fix
        got = client.request("tools/call", {"name": "merge", "arguments": {}})
        self.assertEqual((got["error"]["code"], got["error"]["message"]),
                         (-32602, "Unknown tool: merge"))
        self.assertEqual(client.request("tools/call", {"arguments": {}})["error"]["code"], -32602)
        self.assertEqual(client.request("tools/call")["error"]["code"], -32602)
        for name in ("thread_close", "thread_merge", "merge", "close", "set_aside", "stop"):
            got = client.request("tools/call", {"name": name})
            self.assertEqual(got["error"]["code"], -32602, name)
        self.assertEqual(self.bad(client.tool("unit")), "unit: missing argument 'uid'")
        self.assertIn("unknown argument 'extra'", self.bad(client.tool("map", {"extra": 1})))
        self.assertIn("must be a string", self.bad(client.tool("unit", {"uid": 5})))
        self.assertIn("must be an integer",
                      self.bad(client.tool("thread", {"id": "x", "run": True})))
        self.assertIn("must be an array", self.bad(client.tool("show", {"ops": "label"})))
        got = client.request("tools/call", {"name": "map", "arguments": [1]})
        self.assertEqual(self.bad(got["result"]), "map: the arguments must be an object")
        # a line over the limit is refused whole and the session goes on
        client.send_raw(b"x" * (mcp.MAX_LINE + 10) + b"\n")
        self.assertEqual(client.recv()["error"]["code"], -32600)
        self.assertEqual(client.request("ping")["result"], {})
        out, err = client.close()
        for line in out.splitlines():
            self.assertEqual(json.loads(line)["jsonrpc"], "2.0", "stdout carries only JSON-RPC")
        self.assertIn("ready", err)
        self.assertNotIn('"jsonrpc"', err, "stderr is the log, never the protocol")

    def test_batches_are_served_to_the_versions_that_have_them_and_refused_to_the_rest(self):
        req = {"jsonrpc": "2.0", "method": "ping"}
        for version in ("2025-03-26", "2024-11-05"):
            client = self.start()
            client.initialize(version)
            ping = lambda n: dict(req, id=n)                       # noqa: E731
            client.send([ping(1), {"jsonrpc": "2.0", "method": "notifications/initialized"},
                         {"jsonrpc": "2.0", "id": "two", "method": "tools/list"},
                         {"jsonrpc": "2.0", "id": 3, "method": "no/such"}, 5,
                         {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                          "params": {"name": "unit", "arguments": {}}},
                         {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                          "params": {"name": "map", "arguments": {}}}])
            got = client.recv()
            self.assertIsInstance(got, list, version)
            self.assertEqual([r["id"] for r in got], [1, "two", 3, None, 4, 6],
                             "in order; none for the notification; the bad one answered in place")
            self.assertEqual(got[0]["result"], {})
            self.assertEqual(len(got[1]["result"]["tools"]), len(TOOLS))
            self.assertEqual(got[2]["error"]["code"], -32601)
            self.assertEqual(got[3]["error"]["code"], -32600)
            self.assertEqual(got[4]["result"]["content"][0]["text"], "unit: missing argument 'uid'")
            self.assertIn("planets", json.loads(got[5]["result"]["content"][0]["text"]))
            # nothing at all comes back for a batch of notifications: the next line is the ping's
            client.send([{"jsonrpc": "2.0", "method": "notifications/initialized"},
                         {"jsonrpc": "2.0", "method": "no/such"}])
            client.send(ping(9))
            self.assertEqual(client.recv(), {"jsonrpc": "2.0", "id": 9, "result": {}})
            # an empty batch is one error (an object, not an array); so is an initialize in a batch
            client.send([])
            got = client.recv()
            self.assertEqual((type(got), got["error"]["code"]), (dict, -32600))
            self.assertIn("empty", got["error"]["message"])
            client.send([ping(10), {"jsonrpc": "2.0", "id": 11, "method": "initialize",
                                    "params": {"protocolVersion": version}}])
            got = client.recv()
            self.assertEqual([r["id"] for r in got], [10, 11])
            self.assertIn("result", got[0])
            self.assertEqual(got[1]["error"]["code"], -32600)
            self.assertIn("not part of a batch", got[1]["error"]["message"])
            client.send([ping(n) for n in range(mcp.MAX_BATCH + 1)])
            self.assertIn("at most", client.recv()["error"]["message"])
            client.send([ping(n) for n in range(mcp.MAX_BATCH)])
            self.assertEqual(len(client.recv()), mcp.MAX_BATCH)
        # 2025-06-18 took batching out: its sessions, and one that has not said which it speaks,
        # get one error and nothing runs
        client = self.start()
        client.send([dict(req, id=1)])
        self.assertIn("initialize first", client.recv()["error"]["message"])
        client.initialize("2025-06-18")
        client.send([dict(req, id=2), dict(req, id=3)])
        got = client.recv()
        self.assertEqual((type(got), got["error"]["code"]), (dict, -32600))
        self.assertIn("2025-06-18", got["error"]["message"])
        self.assertEqual(client.request("ping")["result"], {})
        client = self.start()
        client.initialize("2031-01-01")                       # a stranger's version: our newest
        client.send([dict(req, id=1)])
        self.assertEqual(client.recv()["error"]["code"], -32600)

    def test_with_no_map_a_tool_is_an_error_with_the_reason_and_the_server_stays_up(self):
        gone = os.path.join(self.root, "nowhere", "bridge-1.json")
        client = self.start(env=self.env(BRIDGE_LIVE_PATH=gone))
        self.assertEqual(client.initialize()["result"]["serverInfo"]["name"], "container-map")
        self.assertEqual(len(client.request("tools/list")["result"]["tools"]), len(TOOLS))
        for name, args in (("map", {}), ("threads", {}), ("page", {"title": "t", "html": "<p>x"}),
                           ("focus", {"uid": "area:ops"})):
            said = self.bad(client.tool(name, args))
            self.assertIn("no running map found", said, name)
            self.assertIn(gone, said)
            self.assertIn("serve", said, "it says how to start one")
        # a live file that is not private is not trusted
        loose = os.path.join(self.root, "loose.json")
        with open(self.live, encoding="utf-8") as handle:
            with open(loose, "w", encoding="utf-8") as out:
                out.write(handle.read())
        os.chmod(loose, 0o644)
        client = self.start(env=self.env(BRIDGE_LIVE_PATH=loose))
        self.assertIn("not a private live file", self.bad(client.tool("map")))
        # a live file of a map that is not there any more (its port refuses)
        stale = os.path.join(self.root, "stale.json")
        with open(self.live, encoding="utf-8") as handle:
            info = json.load(handle)
        info["port"] = api.free_port()
        with open(stale, "w", encoding="utf-8") as out:
            json.dump(info, out)
        os.chmod(stale, 0o600)
        client = self.start(env=self.env(BRIDGE_LIVE_PATH=stale))
        said = self.bad(client.tool("map"))
        self.assertIn(f"no map answers on 127.0.0.1:{info['port']}", said)
        self.assertNotIn(info["agent_token"], said)
        # the same server finds the map once it is there: the file is read again on the next call
        with open(stale, "w", encoding="utf-8") as out:
            json.dump(dict(info, port=self.app.port), out)
        self.assertIn("planets", self.good(client.tool("map")))

    def test_the_command_runs_the_same_server_and_refuses_a_bad_port(self):
        client = self.start([sys.executable, CLI, "mcp"])
        self.assertEqual(client.initialize()["result"]["serverInfo"]["name"], "container-map")
        self.assertIn("planets", self.good(client.tool("map")))

        def run(*argv, command=CLI):
            return subprocess.run([sys.executable, command, *argv], capture_output=True,
                                  text=True, env=self.env(), cwd=self.root, timeout=30,
                                  stdin=subprocess.DEVNULL)
        done = run("mcp", "--help")
        self.assertEqual(done.returncode, 0)
        self.assertIn("claude mcp add container-map -- container-map mcp", done.stdout)
        done = run("mcp", "--port", "seven")
        self.assertEqual(done.returncode, 2)
        self.assertIn("port must be a number", done.stderr)
        done = run(command=MCP)
        self.assertEqual(done.returncode, 0, "the end of stdin ends it, quietly")
        self.assertEqual(done.stdout, "")
        elsewhere = self.env(BRIDGE_LIVE_PATH=os.path.join(self.root, "nowhere.json"))
        client = self.start([sys.executable, CLI, "mcp", "--live-file", self.live], elsewhere)
        self.assertIn("planets", self.good(client.tool("map")), "--live-file beats the environment")


class Tools(Session):
    def setUp(self):
        super().setUp()
        self.client = self.start()
        self.client.initialize()

    def test_the_map_a_planet_and_the_threads_are_read_through_the_agent_api(self):
        got = self.good(self.client.tool("map"))
        self.assertEqual({p["uid"] for p in got["planets"]},
                         {"area:api", "area:ops", "area:docs", "area:site", "area:data"})
        self.assertIsInstance(got["agents"], list)
        self.assertEqual(got["auth"]["note"],
                         "merging, setting aside and closing threads stay with the person")
        self.assertNotIn(self.app.agent_token, json.dumps(got),
                         "the card names the file, never the token")
        unit = self.good(self.client.tool("unit", {"uid": "area:ops"}))
        self.assertEqual((unit["kind"], unit["name"]), ("planet", "ops"))
        self.assertEqual(unit["inside"][0]["uid"], "dir:ops/logs")
        said = self.bad(self.client.tool("unit", {"uid": "area:nowhere"}))
        self.assertEqual(said, "no planet or folder with that uid on the map",
                         "the bridge's words, as they came")
        out = self.client.tool("unit", {"uid": "area:nowhere"})
        self.assertEqual(out["structuredContent"], {"error": said, "status": 404})
        self.assertIn("one uid query parameter", self.bad(self.client.tool("unit", {"uid": "ops"})))
        threads = self.good(self.client.tool("threads"))
        self.assertEqual((threads["tasks"], threads["max_running"]), ([], 4))
        self.assertEqual(threads["runners"][0]["name"], "writer")

    def test_a_thread_is_handed_out_read_followed_up_and_checked_and_never_landed(self):
        target = os.path.join(self.work, "api")
        verdict = ["/bin/sh", "-c", "cat >/dev/null; echo 'looked it over'; echo 'VERDICT: PASS'"]
        self.app.tasks.runners["checker"] = {"cmd": verdict, "max_minutes": 1, "role": "check"}
        task = {"text": "write made.txt", "target": target, "runner": "writer"}
        started = self.good(self.client.tool("thread_start", task))
        tid = started["id"]
        self.assertEqual((started["runner"], started["target"]), ("writer", target))
        done = self.wait_thread(self.client, tid)
        self.assertEqual(done["id"], tid)
        self.assertIn("lines", done)
        self.assertTrue(os.path.exists(os.path.join(target, "made.txt")))
        got = self.good(self.client.tool("thread", {"id": tid, "run": 1, "since": 0}))
        self.assertEqual(got["run"], 1)
        self.assertEqual(self.good(self.client.tool("threads"))["tasks"][0]["id"], tid)
        again = self.good(self.client.tool("thread_followup", {"id": tid, "text": "once more"}))
        self.assertEqual(again["id"], tid)
        self.wait_thread(self.client, tid)
        said = self.bad(self.client.tool("thread_check", {"id": tid, "runner": "writer"}))
        self.assertIn("does work but does not check", said)
        checked = self.good(self.client.tool("thread_check", {"id": tid, "runner": "checker"}))
        self.assertEqual(checked["id"], tid)
        end = time.monotonic() + 30
        while time.monotonic() < end:
            seen = self.good(self.client.tool("thread", {"id": tid}))
            if (seen.get("last_verdict") or {}).get("verdict"):
                break
            time.sleep(0.1)
        self.assertEqual(seen["last_verdict"]["verdict"], "PASS",
                         "the check ran and its verdict is read through `thread`")
        # nothing of landing is in the tools, and the routes behind it refuse the agent token
        self.assertEqual(self.http("POST", "/task/close", {"id": tid}, self.agent)[0], 403)
        self.assertNotIn("merged", self.good(self.client.tool("thread", {"id": tid}))["status"])
        # refusals are the bridge's own sentences
        said = self.bad(self.client.tool("thread_start", dict(task, verify="true")))
        self.assertEqual(said, "verify is set from the page; "
                               "agent_verify in container.json enables it for agents")
        said = self.bad(self.client.tool("thread_start", dict(task, runner="nobody")))
        self.assertTrue(said and not said.startswith("the map answered"), said)
        for name, args in (("thread", {"id": NOBODY}),
                           ("thread_followup", {"id": NOBODY, "text": "x"}),
                           ("thread_check", {"id": NOBODY, "runner": "writer"})):
            said = self.bad(self.client.tool(name, args))
            self.assertTrue(said and not said.startswith("the map answered"), (name, said))

    def test_a_show_a_page_and_a_focus_reach_the_map_with_their_author(self):
        card = {"op": "card", "title": "Tidy-up", "rows": [["files", "120"]], "bar": [3, 9]}
        show = {"at": "area:ops", "by": "Claude Code", "id": "board",
                "ops": [card, {"op": "teleport"}]}
        got = self.good(self.client.tool("show", show))
        self.assertEqual((got["id"], got["skipped"]), ("board", ["teleport"]))
        self.assertIn("not an op the map draws", got["errors"][0])
        shows = self.http("GET", "/tasks", None, self.ui())[1]["shows"]
        self.assertEqual([(s["id"], s["by"]) for s in shows], [("board", "Claude Code")])
        # nothing drawn at all is a failure, with the reasons
        out = self.client.tool("show", {"at": "area:ops", "ops": [{"op": "teleport"}]})
        self.assertIs(out["isError"], True)
        self.assertEqual(self.text(out)["skipped"], ["teleport"])
        chart = {"op": "chart", "label": "x", "values": [1, "2"]}
        said = self.bad(self.client.tool("show", {"ops": [chart], "at": "area:ops"}))
        self.assertEqual(said, "chart values must be finite numbers")
        html = ("<!doctype html><title>x</title><h1 id=mark>é 😀</h1>"
                "<script>document.title='ran'</script>")
        page = {"title": "Latency é", "html": html, "at": "area:ops", "by": "Claude Code",
                "ttl": 3600, "id": "latency"}
        got = self.good(self.client.tool("page", page))
        self.assertEqual((got["id"], got["show"]["ttl"], got["show"]["ops"][0]["title"]),
                         ("latency", 3600, "Latency é"))
        self.assertEqual(got["show"]["ops"][0]["bytes"], len(html.encode("utf-8")))
        self.assertNotIn("mark", json.dumps(got), "no reply carries the page's source")
        status, headers, body = self.frame("latency")
        self.assertEqual((status, body), (200, html),
                         "the page reaches the person's session whole, non-ASCII included")
        self.assertIn("sandbox allow-scripts", headers["content-security-policy"])
        plain = self.good(self.client.tool("page", {"title": "No place", "html": "<p>a"}))
        self.assertEqual(plain["show"]["ops"][0]["at"], "place:pages")
        said = self.bad(self.client.tool("page", {"title": "t", "html": "a\x1bb"}))
        self.assertIn("control characters", said)
        huge = "a" * (bridge.PAGE_BYTES + 1)
        said = self.bad(self.client.tool("page", {"title": "t", "html": huge}))
        self.assertIn(f"page: html is {bridge.PAGE_BYTES + 1} bytes", said)
        out = self.client.tool("page", {"title": "far", "html": "<p>a", "at": "area:nowhere"})
        self.assertIn("is not on the map", self.bad(out))
        for n in range(5):                                  # five pages: the oldest page goes
            self.good(self.client.tool("page", {"title": f"p{n}", "html": "<p>a", "id": f"pg{n}"}))
        self.assertEqual(self.frame("latency", rev=1)[0], 404)
        # focus: the one control it has
        got = self.good(self.client.tool("focus", {"uid": "area:ops"}))
        action = {"type": "focus", "target": "area:ops", "by": "an agent"}
        self.assertEqual((got["status"], got["command"]["action"]), ("queued", action))
        self.assertEqual(self.app.queue[-1]["action"], action)
        time.sleep(bridge.CONTROL_INTERVAL + 0.02)
        got = self.good(self.client.tool("focus", {"uid": "area:ops", "by": "mcp agent"}))
        self.assertEqual(got["command"]["action"]["by"], "mcp agent", "the map names who moved the view")
        time.sleep(bridge.CONTROL_INTERVAL + 0.02)
        said = self.bad(self.client.tool("focus", {"uid": "area:nowhere"}))
        self.assertIn("not on the map", said)
        # clear
        self.assertEqual(self.good(self.client.tool("show_clear", {"id": "pg4"})),
                         {"ok": True, "id": "pg4"})
        self.assertEqual(self.bad(self.client.tool("show_clear", {"id": "pg4"})),
                         "no live show with that id")
        audit = [r for r in self.audit() if r["event"] in ("show", "control-focus", "show-clear")]
        self.assertTrue(audit and all(r["event"] != "show" or r["by"] for r in audit))

    def test_the_file_is_read_again_after_a_restart_and_the_token_is_in_nothing_printed(self):
        first = self.app.agent_token
        self.assertIn("planets", self.good(self.client.tool("map")))   # the file is read once
        # a restart with a new token: the old one is refused, the file is read again, the call
        # goes through
        self.app.agent_token = "second-" + first[::-1]
        self.app.write_live()
        self.assertIn("planets", self.good(self.client.tool("map")))
        # a restart on a new port: the old one refuses the connection, the file is read again
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = bridge.BridgeHTTPServer((bridge.LOOPBACK, 0), bridge.Handler, self.app)
        self.app.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.app.agent_token = "third-" + first
        self.app.write_live()
        self.assertIn("planets", self.good(self.client.tool("map")))
        # a wrong token that the file still names is the map's own 403, as it said it
        self.app.agent_token = "fourth-" + first
        with open(self.live, encoding="utf-8") as handle:
            wrong = json.load(handle)
        wrong["agent_token"] = "not-the-token-" + first
        with open(self.live, "w", encoding="utf-8") as handle:
            json.dump(wrong, handle)
        self.assertEqual(self.bad(self.client.tool("map")), "agent token or UI session required")
        self.app.write_live()
        self.assertIn("planets", self.good(self.client.tool("map")))
        # a page and a refusal on top of that, and no token is in anything the server printed
        self.good(self.client.tool("page", {"title": "t", "html": "<p>a"}))
        self.bad(self.client.tool("unit", {"uid": "area:nowhere"}))
        out, err = self.client.close()
        tokens = (first, "second-" + first[::-1], "third-" + first, "fourth-" + first,
                  "not-the-token-" + first)
        for token in tokens:
            self.assertNotIn(token, out, "stdout")
            self.assertNotIn(token, err, "stderr")

    def test_the_token_is_scrubbed_from_what_the_client_sent_back_at_it_too(self):
        token = self.app.agent_token
        self.good(self.client.tool("map"))                  # the server knows the token now
        # the client's own words come back in errors and in the log: every one of them through the
        # one function, the early returns of validation and of initialize included
        client = self.start()
        client.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "agent " + token, "version": "1"}}})
        client.recv()
        client.initialize("2025-03-26")                     # (a second initialize, same process)
        self.good(client.tool("map"))
        got = client.request("tools/call", {"name": "tool-" + token, "arguments": {}})
        self.assertEqual(got["error"]["message"], "Unknown tool: tool-<token>")
        got = client.request("no/such/" + token)
        self.assertEqual(got["error"]["message"], "Method not found: no/such/<token>")
        said = self.bad(client.tool("map", {"arg-" + token: 1}))
        self.assertIn("unknown argument 'arg-<token>'", said)
        said = self.bad(client.tool("unit", {"uid": token}))     # the map's own refusal of it
        self.assertNotIn(token, said)
        client.send({"jsonrpc": "2.0", "id": 50, "method": "tools/call",
                     "params": {"name": "unit", "arguments": {"uid": 5, token: 1}}})
        self.assertNotIn(token, json.dumps(client.recv()))
        client.send([{"jsonrpc": "2.0", "id": 51, "method": "m-" + token},
                     {"jsonrpc": "2.0", "id": token, "method": "ping"}])
        got = client.recv()
        self.assertEqual(got[0]["error"]["message"], "Method not found: m-<token>")
        self.assertEqual(got[1]["id"], "<token>", "even an id the client chose")
        cut = '{"jsonrpc": "2.0", "id": 1, "method": "ping", "x": "%s"' % token
        client.send_raw(cut.encode())
        client.send_raw(b"\n")                              # a line cut off: a parse error
        self.assertEqual(client.recv()["error"]["code"], -32700)
        out, err = client.close()
        self.assertNotIn(token, out, "stdout")
        self.assertNotIn(token, err, "stderr")
        self.assertIn("agent <token>", err, "the log line is kept, with the token taken out of it")
        # and the same function, whatever the token looks like in JSON
        mcp.TOKENS.update(("a\"b\\c", json.dumps("a\"b\\c")[1:-1]))
        try:
            line = io.BytesIO()
            self.assertTrue(mcp.emit(line, {"x": 'say a"b\\c twice'}))
            self.assertNotIn(b"a\\\"b", line.getvalue())
            self.assertIn(b"<token>", line.getvalue())
        finally:
            mcp.TOKENS.difference_update(("a\"b\\c", json.dumps("a\"b\\c")[1:-1]))

    def test_a_token_that_straddles_a_cut_is_scrubbed_before_the_cut(self):
        token = self.app.agent_token
        pieces = [token[i:i + 8] for i in range(len(token) - 7)]       # every 8 characters of it
        client = self.start()           # it reads the live file as it starts: it knows the token
        # each of these is cut by the server before it is repeated (60, 20 and 80 characters), and
        # each is built so that the cut falls inside the token
        client.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "v" * 10 + token, "capabilities": {},
            "clientInfo": {"name": "x" * (60 - 42) + token, "version": "1"}}})
        client.recv()
        got = client.request("tools/call", {"name": "t" * (80 - 42) + token, "arguments": {}})
        self.assertEqual(got["error"]["message"], "Unknown tool: " + "t" * 38 + "<token>")
        got = client.request("m" * (80 - 42) + token)
        self.assertEqual(got["error"]["message"], "Method not found: " + "m" * 38 + "<token>")
        got = client.request("m" * (80 - 5) + token)        # the cut would fall after five of it
        self.assertEqual(got["error"]["message"], "Method not found: " + "m" * 75 + "<token>"[:5])
        out, err = client.close()
        self.assertIn("client " + "x" * 18 + "<token>", err)
        self.assertIn("protocol " + "v" * 10 + "<token>"[:10], err, "cut at 20, after the scrub")
        for piece in pieces + [token]:
            self.assertNotIn(piece, out, f"stdout holds {piece!r}")
            self.assertNotIn(piece, err, f"stderr holds {piece!r}")

    def test_a_token_in_a_threads_log_is_scrubbed_before_it_leaves(self):
        token = self.app.agent_token
        leak = ["/bin/sh", "-c", f"cat >/dev/null; echo 'it printed {token} here'; "
                                 "echo '{\"type\":\"result\"}'"]
        self.app.tasks.runners["leaker"] = {"cmd": leak, "max_minutes": 1}
        self.good(self.client.tool("map"))                  # the server knows the token now
        task = {"text": "x", "target": os.path.join(self.work, "api"), "runner": "leaker"}
        tid = self.good(self.client.tool("thread_start", task))["id"]
        done = json.dumps(self.wait_thread(self.client, tid))
        self.assertIn("it printed <token> here", done, "the words stay, the token in them does not")
        raw = self.http("GET", f"/agent/task?id={tid}", None, self.agent)[1]
        self.assertIn(token, json.dumps(raw), "the bridge had it in the log: the server held "
                                              "it back")
        out, err = self.client.close()
        self.assertNotIn(token, out)
        self.assertNotIn(token, err)


class Naming(unittest.TestCase):
    def test_nothing_of_ours_is_called_mcp_because_that_is_the_sdks_package(self):
        import importlib.util
        self.assertFalse(os.path.exists(os.path.join(HERE, "mcp.py")))
        with open(os.path.join(HERE, "pyproject.toml"), encoding="utf-8") as handle:
            modules = re.findall(r'^\s+"([a-z_]+)",$', handle.read(), re.M)
        self.assertIn("container_map_mcp", modules)
        self.assertNotIn("mcp", modules)
        found = importlib.util.find_spec("mcp")     # the SDK's, where it is installed, never ours
        self.assertTrue(found is None or not str(found.origin or "").startswith(HERE), found)


class Retry(unittest.TestCase):
    """When the live file is read again and a call goes out twice (the server's logic, with the
    network taken out)."""

    def rig(self, infos, replies):
        server = mcp.Bridge({})
        located, sent = iter(infos), []
        server._locate = lambda: next(located)

        def send(info, method, path, body, timeout):
            sent.append((info["port"], method))
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        server._send = send
        return server, sent

    def info(self, port, token="t"):
        return {"port": port, "agent_token": token}

    def test_a_get_goes_again_when_the_file_names_another_map(self):
        for err in (ConnectionRefusedError(111, "refused"), ConnectionResetError(104, "reset")):
            server, sent = self.rig([self.info(1), self.info(2)], [err, (200, {"ok": 1})])
            self.assertEqual(server.call("GET", "/agent/map"), (200, {"ok": 1}))
            self.assertEqual(sent, [(1, "GET"), (2, "GET")])

    def test_a_get_goes_once_when_the_file_says_nothing_new(self):
        refused = ConnectionRefusedError(111, "refused")
        server, sent = self.rig([self.info(1), self.info(1)], [refused])
        with self.assertRaises(mcp.Failed) as caught:
            server.call("GET", "/agent/map")
        self.assertIn("no map answers on 127.0.0.1:1 (refused)", str(caught.exception))
        self.assertEqual(sent, [(1, "GET")])

    def test_a_post_goes_again_only_if_nothing_was_sent_or_the_map_said_no(self):
        refused = ConnectionRefusedError(111, "refused")
        server, sent = self.rig([self.info(1), self.info(2)], [refused, (200, {})])
        self.assertEqual(server.call("POST", "/agent/task", {}), (200, {}))
        self.assertEqual(sent, [(1, "POST"), (2, "POST")])
        server, sent = self.rig([self.info(1, "old"), self.info(1, "new")],
                                [(403, {"error": "no"}), (202, {"ok": 1})])
        self.assertEqual(server.call("POST", "/control", {}), (202, {"ok": 1}))
        self.assertEqual(sent, [(1, "POST"), (1, "POST")])
        # a connection that broke after the request left may have been done: never sent twice
        for broke in (ConnectionResetError(104, "reset"), TimeoutError("timed out")):
            server, sent = self.rig([self.info(1), self.info(2)], [broke, (200, {})])
            with self.assertRaises(mcp.Failed):
                server.call("POST", "/agent/task", {})
            self.assertEqual(sent, [(1, "POST")])

    def test_a_403_with_the_same_token_stands_and_the_next_call_reads_the_file_again(self):
        server, sent = self.rig([self.info(1), self.info(1), self.info(1, "fresh")],
                                [(403, {"error": "agent token required"}), (200, {"ok": 1})])
        denied = (403, {"error": "agent token required"})
        self.assertEqual(server.call("GET", "/agent/map"), denied)
        self.assertEqual(sent, [(1, "GET")])
        self.assertEqual(server.call("GET", "/agent/map"), (200, {"ok": 1}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
