#!/usr/bin/env python3
"""container-map mcp: the map's agent API as tools for any agent that speaks MCP (docs/MCP.md).

A stdio server: newline-delimited JSON-RPC 2.0 on stdin and stdout, and everything it logs goes
to stderr (stdout is the protocol). It finds the running map the way `container-map show` does
(the live file: --live-file, $BRIDGE_LIVE_PATH, else --root and --port) and calls the agent routes
of docs/AGENT-API.md with that file's token. The token stays in this process: no tool result,
error or log line carries it. Landing work (merge, close, set aside, stop) is not here, as it is
not in the agent API: it stays with the person on the map. Stdlib only."""
import argparse
import http.client
import json
import os
import secrets
import sys
import urllib.parse

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:        # run as a script from anywhere: cli.py, workflows.py sit beside
    sys.path.insert(0, HERE)
import workflows  # noqa: E402

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")     # newest first: a stranger gets #1
# JSON-RPC batches (an array of messages in, an array of answers out): 2025-03-26 added them and
# 2025-06-18 took them out again, so a session that negotiated that one is refused its batches
BATCH_PROTOCOLS = ("2025-03-26", "2024-11-05")
MAX_BATCH = 64                  # messages in one batch
MAX_LINE = 4 * 1024 * 1024      # one message; a 256 KiB page written as \uXXXX is under 1 MiB
MAX_REPLY = 8 * 1024 * 1024
READ_SECONDS, WRITE_SECONDS = 30.0, 120.0   # a thread can take a while to set up (a worktree)
OK_STATUS = (200, 201, 202)
B = workflows.BOUNDS
LANDING = ("Landing work (merge, close, set aside, stop) is NOT available to agents: it stays with "
           "the person on the map.")
INSTRUCTIONS = (
    "container-map is a live map of the person's workspace: planets are folders they chose, "
    "threads are tasks handed to agents, shows are things drawn on the map for a while. Start "
    "with `map` for the planets (uids), the agents you may hand work to and the open threads. "
    "You can read the map (map, unit, threads, thread), hand out and check threads "
    "(thread_start, thread_followup, thread_check) and draw on the map (show, page, show_clear, "
    "focus). " + LANDING + " Everything you draw names you as its author and never starts, "
    "stops or changes work. `page` puts your own self-contained HTML page on the map, which the "
    "person opens in a sandbox: all its JS and CSS inline, no network.")


class Failed(Exception):
    """The map could not be reached: the sentence that says why (a tool result, isError)."""


TOKENS = set()                  # every agent token seen (Bridge adds them): none leaves


def scrub(text):
    """text with every token seen replaced. The one function between this server and its stdout and
    stderr: log() and emit() both end in it, so no path (a result, an error that reflects what the
    client sent, a log line) can skip it."""
    for token in TOKENS:
        text = text.replace(token, "<token>")
    return text


def clip(text, limit):
    """text cut to limit characters, after its tokens are taken out: a token that straddles the cut
    must not leave a piece of itself behind. Every place that repeats what the client or the map
    sent cuts it here."""
    return scrub(str(text))[:limit]


def log(text):
    line = "container-map mcp: " + str(text).replace("\n", " ")
    sys.stderr.write(clip(line, 500) + "\n")
    sys.stderr.flush()


# ---- the map: found by its live file, called with its token --------------------------------------
class Bridge:
    def __init__(self, opts):
        self.opts = opts
        self.info = None            # the live file's JSON, kept until a call fails

    def _locate(self):
        import cli                  # the finder `container-map show` uses
        try:
            _live, info = cli._live_info(self.opts)
        except cli.Refused as exc:
            raise Failed(exc.message) from None
        token = str(info["agent_token"])
        TOKENS.update((token, json.dumps(token)[1:-1]))     # as it is, and as JSON writes it
        return info

    def warm(self):
        """Read the live file now, if there is one, so its token is known (and scrubbed) from the
        first byte this server writes, not only from the first call it makes. No map is no error."""
        try:
            self.info = self._locate()
        except Failed:
            pass

    def _send(self, info, method, path, body, timeout):
        headers = {"Authorization": "Bearer " + str(info["agent_token"])}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")      # pure ASCII: any text goes through
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=timeout)
        try:
            conn.request(method, path, body=payload, headers=headers)
            reply = conn.getresponse()
            data = reply.read(MAX_REPLY + 1)
        finally:
            conn.close()
        if len(data) > MAX_REPLY:
            raise Failed(f"the map's reply is over {MAX_REPLY} bytes")
        try:
            value = json.loads(data.decode("utf-8"))
        except ValueError:
            value = {"error": clip(data.decode("utf-8", "replace"), 200)}
        return reply.status, value

    def call(self, method, path, body=None, timeout=READ_SECONDS):
        """(status, JSON) of one call. The live file is read on the first call and again after a
        failure that a restart explains: a connection error, or a 401/403. The call goes out once
        more only if the file now names another port or token, and a POST only when nothing was
        sent (a refused connection) or the map said no."""
        seen, last = None, None
        for attempt in (1, 2):
            if self.info is None:
                self.info = self._locate()
            info = self.info
            if attempt == 2 and (info["port"], info["agent_token"]) == seen:
                break               # the file says nothing new: the first failure stands
            try:
                status, value = self._send(info, method, path, body, timeout)
            except (OSError, http.client.HTTPException) as exc:
                reason = getattr(exc, "strerror", None) or exc
                last = ("down", f"no map answers on 127.0.0.1:{info['port']} ({reason})")
                safe = method == "GET" or isinstance(exc, ConnectionRefusedError)
            else:
                if status not in (401, 403):
                    return status, value
                last, safe = ("refused", (status, value)), True
            seen = (info["port"], info["agent_token"])
            self.info = None
            if not safe:
                break
        if last[0] == "down":
            raise Failed(last[1])
        return last[1]

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body):
        return self.call("POST", path, body, WRITE_SECONDS)


# ---- tool results --------------------------------------------------------------------------------
def compact(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def result(value, error=False):
    out = {"content": [{"type": "text", "text": compact(value)}]}
    if isinstance(value, dict):
        out["structuredContent"] = value
    if error:
        out["isError"] = True
    return out


def refused(message, status=None):
    """A tool that did not work: the map's own sentence, word for word (and its status, for a
    program to read)."""
    out = {"content": [{"type": "text", "text": message}], "isError": True}
    if status is not None:
        out["structuredContent"] = {"error": message, "status": status}
    return out


def answer(status, value):
    if status in OK_STATUS:
        return result(value)
    message = value.get("error") if isinstance(value, dict) else None
    return refused(message if isinstance(message, str) else f"the map answered {status}", status)


def shown(status, value):
    """A show or a page the map took but drew nothing of (every op skipped) is a failure, with
    the reasons in it."""
    if (status == 200 and isinstance(value, dict) and value.get("id") is None
            and (value.get("errors") or value.get("skipped"))):
        return result(value, error=True)
    return answer(status, value)


# ---- the tools -----------------------------------------------------------------------------------
def _s(text, **extra):
    return dict({"type": "string", "description": text}, **extra)


def _i(text, **extra):
    return dict({"type": "integer", "description": text}, **extra)


UID = _s("A planet `area:<name>` or a folder `dir:<path>` (planet uids are in `map`, folder uids "
         "in a planet's `inside[].uid`).", pattern="^(area|dir):.+")
WHERE = _s("Where it is drawn: a uid (`area:notes`, `dir:...`), `place:<name>` (<= 24 characters) "
           "or an absolute folder path inside the map.")
BY = _s("Your name, shown on it (<= 24 characters; default 'an agent').", maxLength=24)
SHOW_ID = _s("A show with the same id is replaced in place (a live card).",
             pattern="^[a-z0-9][a-z0-9-]{0,39}$")
THREAD = _s("The thread id.")
OP_SCHEMAS = workflows.schema()["show_ops"]
OP_NAMES = sorted(set(OP_SCHEMAS) - {"every op"})
OPS_TEXT = compact({k: v for k, v in OP_SCHEMAS.items() if k != "page"})   # `page` has its own tool
TTL_SHOW = (f"Seconds, {B['show_ttl_min_s']}-{B['show_ttl_max_s']}, default 300 (a show with a "
            f"page: up to {B['page_ttl_max_s']}, default {B['page_ttl_default_s']}).")
MODEL = _s("Optional: one of that agent's models.")


def _tool(name, title, description, properties=None, required=(), read_only=False):
    schema = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return {"name": name, "title": title, "description": description, "inputSchema": schema,
            "annotations": {"readOnlyHint": read_only, "destructiveHint": False,
                            "openWorldHint": False}}


TOOLS = [
    _tool("map", "Read the map",
          "Read the whole container map: every planet (a folder the person chose) with its "
          "purpose, size, problems and week-over-week trend, the open threads (up to 40), the "
          "agents you may hand work to, decisions waiting on the person and the calls you can "
          "make. Read-only. Start here: it names the uids (`area:<name>`) and runner names the "
          "other tools take.", read_only=True),
    _tool("unit", "Read a planet or folder",
          "Read one planet or folder as a card: what it holds, its git state, the agent seats "
          "and threads there, its problems and cron lines, the folders inside (with their `dir:` "
          "uids), the biggest files and a text brief you can paste. Read-only. 404 when nothing "
          "on the map has that uid.", {"uid": UID}, ["uid"], True),
    _tool("threads", "List threads",
          "List every open thread and the newest 200 closed ones (newest first): status, agent, "
          "target, verify and check results, merge blockers and evidence, plus the agents on "
          "offer. Read-only (reading it also lets the retry loop send a failed thread that has "
          "retries left back to its agent).", read_only=True),
    _tool("thread", "Read a thread",
          "Read one thread: its summary and the log of a run, parsed into lines. `run` picks a "
          "run (default the latest); `since` is a byte offset into that run's log (default 0): "
          "while `more` is true or the status is running, call again with the returned `offset` "
          "as `since` to follow it. Read-only.",
          {"id": THREAD, "run": _i("A run number, 0 or more.", minimum=0),
           "since": _i("A byte offset into the run's log, 0 or more.", minimum=0)},
          ["id"], True),
    _tool("thread_start", "Hand a task to an agent",
          "Hand a task to an agent as a thread: in a git repo (with a commit) it works in its own "
          "worktree, and the person reviews the result and merges it. `text` is the task "
          "(<= 8000 characters; say what to do and how to verify it); `target` is an absolute "
          "folder inside the roots the map shows; `runner` is one of `agents[].name` from `map` "
          "that does work (not a check-only one); `model` is optional, one of that agent's "
          "models. `verify` (one shell line that must exit 0), `retries` (0-3) and `setup` (one "
          "shell line) are refused unless the person turned `agent_verify` on in "
          "container.json. At most one new thread every ~2 seconds and 4 running at once. "
          + LANDING,
          {"text": _s("What to do (<= 8000 characters).", maxLength=8000),
           "target": _s("An absolute folder path inside the map's roots (a file path is cut to "
                        "its folder)."),
           "runner": _s("An agent name from `map` (`agents[].name`), one that does work."),
           "model": MODEL,
           "isolate": {"type": "boolean", "description": "Ignored for an agent: a thread always "
                                                          "asks for its own worktree."},
           "verify": _s("One shell line that must exit 0 in the thread's folder (needs "
                        "agent_verify).", maxLength=400),
           "retries": _i("0-3 automatic retries after a failed verify (needs agent_verify and "
                         "verify).", minimum=0, maximum=3),
           "setup": _s("One shell line run in the new worktree before the agent (needs "
                       "agent_verify).", maxLength=400)},
          ["text", "target", "runner"]),
    _tool("thread_followup", "Reply to a thread",
          "Send a stopped thread's agent back to work with a reply. 409 while the agent is still "
          "working, when the thread is closed, its worktree is gone or its agent is no longer "
          "offered. " + LANDING,
          {"id": THREAD, "text": _s("What to do next.")}, ["id", "text"]),
    _tool("thread_check", "Have another agent check a thread",
          "Have another agent read a thread's work: read-only, and it ends in a line "
          "`VERDICT: PASS` or `VERDICT: FAIL`. `runner` must be an agent whose role is not "
          "work-only (a different model family than the one that did the work gives a real "
          "second opinion). Same 409 cases as thread_followup, and 409 when nothing has run "
          "yet. Read the verdict with `thread`. " + LANDING,
          {"id": THREAD, "runner": _s("An agent name from `map` that may check."),
           "model": MODEL, "text": _s("Optional: what to look at.")}, ["id", "runner"]),
    _tool("show", "Draw on the map",
          "Paint on the map for a while (memory only, gone when the map restarts). `ops` is up "
          f"to {B['show_items']} ops, each `{{op: <name>, ...}}`; an unknown or bad op is "
          "skipped and named in `skipped`/`errors` and the rest are kept, an out-of-range value "
          f"is clamped and named in `clamped`. A show lives {B['show_ttl_min_s']}-"
          f"{B['show_ttl_max_s']} seconds (default 300), at most {B['shows_live']} are live and "
          "a new one retires the oldest; the same `id` replaces. Every show names you as its "
          "author (`by`), holds no links or buttons and never starts, stops or changes work. "
          "For your own HTML page use the `page` tool. Ops (the schema of `container-map wf "
          "schema`; an op's own `at` overrides the show's): " + OPS_TEXT,
          {"ops": {"type": "array", "maxItems": B["show_items"],
                   "description": "The ops to draw.",
                   "items": {"type": "object", "required": ["op"],
                             "properties": {"op": {"type": "string", "enum": OP_NAMES}}}},
           "at": WHERE, "by": BY,
           "ttl": _i(TTL_SHOW, minimum=B["show_ttl_min_s"], maximum=B["page_ttl_max_s"]),
           "id": SHOW_ID}, ["ops"]),
    _tool("show_clear", "Take a show down",
          "Take one of your shows (or pages) down before its time, by id. 404 when no live show "
          "has that id.",
          {"id": _s("The show id from the reply of `show` or `page`.")}, ["id"]),
    _tool("page", "Put an HTML page on the map",
          "Put your own HTML page on the map: a dashboard, a chart, a bug visualizer. It appears "
          "as a card (at `at`, default `place:pages`) and the person opens it in a sandboxed "
          "panel inside the map when they choose to: a toast tells them it arrived, it never "
          "opens by itself, and a phone shows the card only. `html` is the whole page, at most "
          f"{B['page_bytes']} bytes of UTF-8 with no control characters but newline, return and "
          "tab. Scripts run, but EVERYTHING must be inline: JS in <script> blocks (inline "
          "handlers work too), CSS in <style> "
          "blocks, images and fonts as data: URIs. The page has no network (no fetch, no "
          "external script, stylesheet, font, image or link out), no access to the map, the "
          "bridge or the person's session, and cannot talk back to you: it is a picture, not a "
          f"control surface. `title` is at most {B['page_title']} characters. At most "
          f"{B['pages_live']} pages are live (a new page retires the oldest page); a page lives "
          f"{B['show_ttl_min_s']} seconds to 24 hours (`ttl` in seconds, default "
          f"{B['page_ttl_default_s']}); the same `id` replaces the page in place (a live "
          "dashboard). It names you as its author (`by`) and never starts, stops or changes "
          "work.",
          {"title": _s(f"The card's title (<= {B['page_title']} characters).", minLength=1,
                       maxLength=B["page_title"]),
           "html": _s("The whole page, self-contained."), "at": WHERE, "by": BY,
           "ttl": _i(f"Seconds, {B['show_ttl_min_s']}-{B['page_ttl_max_s']}, default "
                     f"{B['page_ttl_default_s']}.", minimum=B["show_ttl_min_s"],
                     maximum=B["page_ttl_max_s"]),
           "id": SHOW_ID}, ["title", "html"]),
    _tool("focus", "Centre the person's view on a place",
          "Ask the map to centre a planet or place in the person's view. It moves their view "
          "only as their setting \"Agents can move my view\" allows (Off / When idle / Always); "
          "otherwise the page asks them. A uid that is not on the map is a 400. Nothing else "
          "about the person's view or the map can be controlled from here.",
          {"uid": _s("`area:<name>`, `dir:<path>` or `place:<name>`."), "by": BY}, ["uid"]),
]
SCHEMAS = {tool["name"]: tool["inputSchema"] for tool in TOOLS}


def _query(path, **values):
    given = {k: str(v) for k, v in values.items() if v is not None}
    return path + "?" + urllib.parse.urlencode(given)


def run_tool(bridge, name, a):
    """Call the route behind a tool with its arguments; the MCP result."""
    if name == "map":
        return answer(*bridge.get("/agent/map"))
    if name == "unit":
        return answer(*bridge.get(_query("/agent/unit", uid=a["uid"])))
    if name == "threads":
        return answer(*bridge.get("/agent/tasks"))
    if name == "thread":
        path = _query("/agent/task", id=a["id"], run=a.get("run"), since=a.get("since"))
        return answer(*bridge.get(path))
    if name == "thread_start":
        return answer(*bridge.post("/agent/task", a))
    if name == "thread_followup":
        return answer(*bridge.post("/agent/task/followup", a))
    if name == "thread_check":
        return answer(*bridge.post("/agent/task/check", a))
    if name == "show":
        return shown(*bridge.post("/agent/show", a))
    if name == "show_clear":
        return answer(*bridge.post("/agent/show/clear", a))
    if name == "page":
        body = {"ops": [{"op": "page", "title": a["title"], "html": a["html"]}]}
        body.update({k: a[k] for k in ("at", "by", "ttl", "id") if k in a})
        return shown(*bridge.post("/agent/show", body))
    if name == "focus":
        action = {"type": "focus", "target": a["uid"], **({"by": a["by"]} if "by" in a else {})}
        request = {"request_id": "mcp-" + secrets.token_hex(8), "action": action}
        return answer(*bridge.post("/control", request))
    raise KeyError(name)


def check(args, schema):
    """The sentence that says what is wrong with a tool's arguments, else None. The map checks
    the values; this checks the shape, so the reply can name the tool's own arguments."""
    if not isinstance(args, dict):
        return "the arguments must be an object"
    props = schema["properties"]
    for key in schema.get("required", ()):
        if key not in args:
            return f"missing argument {key!r}"
    for key, value in args.items():
        if key not in props:
            return f"unknown argument {key!r} (this tool takes: {', '.join(props) or 'none'})"
        want = props[key]["type"]
        good = {"string": isinstance(value, str), "array": isinstance(value, list),
                "boolean": isinstance(value, bool),
                "integer": isinstance(value, int) and not isinstance(value, bool)}[want]
        if not good:
            article = "an" if want in ("array", "integer") else "a"
            return f"argument {key!r} must be {article} {want}"
    return None


# ---- the protocol --------------------------------------------------------------------------------
def error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def reply_to(id_, result_):
    return {"jsonrpc": "2.0", "id": id_, "result": result_}


class Server:
    def __init__(self, bridge):
        self.bridge = bridge
        self.version = None         # the protocol version this session negotiated

    def handle_batch(self, items):
        """The reply to a JSON array of messages: an array of the answers to the requests in it (the
        notifications in it get none, and a batch of only those gets nothing at all, None). An empty
        array is one error, an invalid message in it is an error in its place, and an initialize is
        never part of a batch. Only a session of a version that has batches may send one."""
        if self.version not in BATCH_PROTOCOLS:
            why = ("this session speaks " + self.version if self.version
                   else "initialize first, on its own")
            return error(None, -32600, f"Invalid Request: batches are not supported ({why})")
        if not items:
            return error(None, -32600, "Invalid Request: an empty batch")
        if len(items) > MAX_BATCH:
            return error(None, -32600, f"Invalid Request: a batch is at most {MAX_BATCH} messages")
        replies = []
        for item in items:
            if isinstance(item, dict) and item.get("method") == "initialize":
                reply = error(item.get("id") if "id" in item else None, -32600,
                              "Invalid Request: initialize is not part of a batch")
            else:
                reply = self.handle(item)
            if reply is not None:
                replies.append(reply)
        return replies or None

    def handle(self, msg):
        """The reply to one JSON-RPC message, or None (a notification, or an answer from the
        client: nothing goes back)."""
        if not isinstance(msg, dict):
            return error(None, -32600, "Invalid Request: a message is a JSON object")
        has_id = "id" in msg
        id_ = msg.get("id")
        if has_id and (isinstance(id_, bool) or not isinstance(id_, (int, str))):
            return error(None, -32600, "Invalid Request: id is a number or a string")
        if "method" not in msg and ("result" in msg or "error" in msg):
            return None             # an answer to a request this server never made
        owner = id_ if has_id else None
        if msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return error(owner, -32600, "Invalid Request: {jsonrpc: \"2.0\", method, ...} is "
                                        "needed")
        method, params = msg["method"], msg.get("params")
        if params is not None and not isinstance(params, (dict, list)):
            return error(owner, -32600, "Invalid Request: params is an object or an array")
        if not has_id:
            return None             # notifications/initialized, cancelled, ...: no reply, ever
        try:
            if method == "initialize":
                return self.initialize(id_, params)
            if method == "ping":
                return reply_to(id_, {})
            if method == "tools/list":
                return reply_to(id_, {"tools": TOOLS})
            if method == "tools/call":
                return self.call(id_, params)
        except Exception as exc:    # never let one message end the session
            log(f"internal error in {method}: {type(exc).__name__}: {exc}")
            return error(id_, -32603, "Internal error")
        return error(id_, -32601, f"Method not found: {clip(method, 80)}")

    def initialize(self, id_, params):
        if not isinstance(params, dict) or not isinstance(params.get("protocolVersion"), str):
            return error(id_, -32602, "initialize takes {protocolVersion, capabilities, "
                                      "clientInfo}")
        import cli
        wanted = params["protocolVersion"]
        self.version = wanted if wanted in PROTOCOLS else PROTOCOLS[0]
        client = params.get("clientInfo")
        if isinstance(client, dict) and isinstance(client.get("name"), str):
            log(f"client {clip(client['name'], 60)}, protocol {clip(wanted, 20)}")
        return reply_to(id_, {
            "protocolVersion": self.version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "container-map", "version": cli.VERSION},
            "instructions": INSTRUCTIONS})

    def call(self, id_, params):
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return error(id_, -32602, "tools/call takes {name, arguments}")
        name, args = params["name"], params.get("arguments")
        if name not in SCHEMAS:
            return error(id_, -32602, f"Unknown tool: {clip(name, 80)}")
        args = {} if args is None else args
        wrong = check(args, SCHEMAS[name])
        if wrong:
            return reply_to(id_, refused(f"{name}: {wrong}"))
        try:
            out = run_tool(self.bridge, name, args)
        except Failed as exc:
            out = refused(str(exc))
        return reply_to(id_, out)


def read_line(stream):
    """The next line of stdin as bytes (None at the end); a line over MAX_LINE is dropped whole
    and comes back as b""."""
    line = stream.readline(MAX_LINE + 1)
    if not line:
        return None
    if len(line) > MAX_LINE and not line.endswith(b"\n"):
        while line and not line.endswith(b"\n"):
            line = stream.readline(MAX_LINE)
        return b""
    return line


def emit(stream, msg):
    """One message (or one batch of them) as one line of ASCII (no character can split a line), with
    every token seen scrubbed out of it; False when the reader has gone."""
    try:
        stream.write((scrub(json.dumps(msg)) + "\n").encode("ascii"))
        stream.flush()
    except BrokenPipeError:
        return False
    return True


def serve(opts, stdin=None, stdout=None):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    bridge = Bridge(opts)
    bridge.warm()
    server = Server(bridge)
    log("ready: JSON-RPC on stdin and stdout, logs here")
    while True:
        raw = read_line(stdin)
        if raw is None:
            return 0
        if raw == b"":
            reply = error(None, -32600, f"Invalid Request: a message is at most {MAX_LINE} bytes")
        elif not raw.strip():
            continue
        else:
            try:
                msg = json.loads(raw.decode("utf-8"))
            except (UnicodeError, ValueError, RecursionError) as exc:
                reply = error(None, -32700, "Parse error: " + clip(exc, 120))
            else:
                reply = (server.handle_batch(msg) if isinstance(msg, list)
                         else server.handle(msg))
        if reply is not None and not emit(stdout, reply):
            return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="container-map mcp",
                                     description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default="", help="the map's folder (default: $CONTAINER_ROOT, "
                        "else the map above the current folder)")
    parser.add_argument("--port", default="", help="the bridge's port (default: $BRIDGE_PORT, "
                        "else the one bridge set up for the root)")
    parser.add_argument("--live-file", default="", help="the bridge's live file (default: "
                        "$BRIDGE_LIVE_PATH, else found from --root and --port)")
    args = parser.parse_args(argv)
    if args.port and not (args.port.isdigit() and 1 <= int(args.port) <= 65535):
        parser.error(f"port must be a number from 1 to 65535, got {args.port!r}")
    opts = {"--root": args.root, "--port": args.port, "--live-file": args.live_file}
    if sys.stdin.isatty():
        log("this speaks MCP on stdin and stdout, for an agent to run: add it to your agent "
            "(docs/MCP.md); Ctrl-D ends it")
    try:
        return serve(opts)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
