# MCP: the map as tools for your agent

`container-map mcp` is an [MCP](https://modelcontextprotocol.io) server for the running map. Add it once to
Claude Code, Codex, Cursor or any agent that speaks MCP, and you can tell that agent "show me X on the map" and it
happens: the agent reads the map, hands out and checks threads, and draws on it, with the same agent API
that `curl` and `container-map show` use ([AGENT-API.md](AGENT-API.md)), and the same limits.

It is a stdio server: newline-delimited JSON-RPC 2.0 on stdin and stdout, stdlib only, nothing to install beyond
container-map. Everything it logs goes to stderr (stdout is the protocol). It reads the map's live file for the
port and the token ([Finding the token](AGENT-API.md#finding-the-token)) and keeps both to itself: no tool result,
error or log line carries the token, and an agent never needs to read the file.

## Add it to your agent

The lines are for you to run or paste; container-map never writes an agent's config.

**Claude Code** (once, for you; `--scope project` shares it through the repo's `.mcp.json`):

```sh
claude mcp add container-map -- container-map mcp
```

**Codex**, in `~/.codex/config.toml`:

```toml
[mcp_servers.container-map]
command = "container-map"
args = ["mcp"]
```

**Cursor and others:** a stdio server whose command is `container-map` and whose argument is `mcp`.

From an unzipped copy rather than an install, use `python3 /path/to/container-map/cli.py mcp` as the command
(Claude Code: `claude mcp add container-map -- python3 /path/to/container-map/cli.py mcp`; Codex: `command =
"python3"`, `args = ["/path/to/container-map/cli.py", "mcp"]`).

### Which map

Like `container-map show`, it finds the map by its live file: `--live-file FILE`, else `$BRIDGE_LIVE_PATH`, else
`--root ROOT` and `--port N` (or `$CONTAINER_ROOT` and `$BRIDGE_PORT`, or the map above the folder the agent was
started in). With one map running you add nothing. With several, name one: `claude mcp add container-map --
container-map mcp --root ~/work --port 7428`.

You can add it before the map is running. Until it is, every tool answers (as a tool error, not a crash) `no
running map found (looked for …); start it with `container-map serve``, and the first call after `serve` finds
it. If the map restarts (a new port or token), the file is read again and the call goes through; a call that
changes something is never sent twice.

## The tools

Every tool returns the map's reply as JSON: in `structuredContent`, and the same compact JSON as text. A refusal
(400, 403, 404, 409, 429, ...) or no map at all is a tool result with `isError: true` whose text is the map's own
sentence, word for word (`no planet or folder with that uid on the map`, `verify is set from the page; agent_verify
in container.json enables it for agents`), so the agent can read it and try again. Bad arguments (a missing
`uid`, a number for a string) are the same kind of result. An unknown tool name is a JSON-RPC error (-32602).

| tool | arguments | what it does |
|---|---|---|
| `map` | none | the whole map: planets with their problems and trend, the open threads (up to 40), the agents on offer, decisions waiting. Read-only. Start here for uids and runner names |
| `unit` | `uid` | one planet (`area:<name>`) or folder (`dir:<path>`) as a card: git state, seats and threads there, problems, crons, the folders inside, a text brief. Read-only |
| `threads` | none | every open thread and the newest 200 closed ones, with status, verify and check results, merge blockers, plus the agents on offer. Read-only |
| `thread` | `id`, `run?`, `since?` | one thread's summary and the log of a run (`since` is a byte offset: poll with the returned `offset`). Read-only |
| `thread_start` | `text`, `target`, `runner`, `model?`, `verify?`, `retries?`, `setup?`, `isolate?` | hand a task to an agent in its own worktree: `text` ≤ 8000 characters, `target` an absolute folder inside the map's roots. `verify`, `retries` and `setup` run a shell line as you, so the map refuses them unless you set `agent_verify` in `container.json`. At most one new thread every ~2 s and 4 running at once |
| `thread_followup` | `id`, `text` | send a stopped thread's agent back to work (409 while it works, when the thread is closed or its worktree is gone) |
| `thread_check` | `id`, `runner`, `model?`, `text?` | have another agent read the work: read-only, ends in `VERDICT: PASS` or `FAIL`; `runner` must be one that may check |
| `show` | `ops`, `at?`, `by?`, `ttl?`, `id?` | paint on the map for a while: up to 16 ops (label, marker, ring, beam, route, radio, fx, card, chart; [the show ops](AGENT-API.md#post-agentshow-post-agentshowclear)), 30-900 s (default 300), at most 8 live, the same `id` replaces. Every show names its author. A show of which nothing could be drawn is a tool error that lists why |
| `page` | `title`, `html`, `at?`, `by?`, `ttl?`, `id?` | put the agent's own HTML page on the map (below) |
| `show_clear` | `id` | take a show or a page down early |
| `focus` | `uid`, `by` | ask the map to centre a planet or place in your view, as your setting "Agents can move my view" allows |

**What no tool can do:** merge, set aside, stop or close a thread (there is no agent route for them, so there is
no tool: they stay with you on the map), set `isolate` off, turn `agent_verify` on, pair a phone, answer a
workflow's gate, or touch a file of yours. `focus` is the one control the server sends to the map, and it can only
centre a view. The tool descriptions say all this to the agent in its own context.

## An agent draws a page

You say to your agent: "put the latency numbers on the map as a chart". The agent calls `map` to learn the planet
uids, then `page`. On the wire (one JSON object per line; `->` is what the agent's client sends, `<-` the reply):

```
-> {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"demo-agent","version":"1"}}}
<- {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18","capabilities":{"tools":{}},"serverInfo":{"name":"container-map","version":"1.0.0"},"instructions":"container-map is a live map of the person's workspace: ..."}}
-> {"jsonrpc":"2.0","method":"notifications/initialized"}
-> {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"page","arguments":{"title":"Stale files","by":"demo-agent","at":"area:ops","ttl":3600,"id":"stale-files","html":"<!doctype html><title>Stale files</title><h1 id=n>12 stale files</h1><script>document.getElementById('n').textContent += ' (checked)'</script>"}}}
<- {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{\"ok\":true,\"id\":\"stale-files\",\"skipped\":[],\"clamped\":[],\"errors\":[],\"expires\":\"2026-10-20T16:51:06Z\",\"show\":{\"id\":\"stale-files\",\"by\":\"demo-agent\",\"at\":\"area:ops\",\"ops\":[{\"op\":\"page\",\"at\":\"area:ops\",\"title\":\"Stale files\",\"bytes\":183}],\"ttl\":3600,\"t\":\"2026-10-20T15:51:06Z\",\"expires\":\"2026-10-20T16:51:06Z\",\"expires_in\":3600}}"}],"structuredContent":{"ok":true,"id":"stale-files","skipped":[],"clamped":[],"errors":[],"expires":"2026-10-20T16:51:06Z","show":{"id":"stale-files","by":"demo-agent","at":"area:ops","ops":[{"op":"page","at":"area:ops","title":"Stale files","bytes":183}],"ttl":3600,"t":"2026-10-20T15:51:06Z","expires":"2026-10-20T16:51:06Z","expires_in":3600}}}}
```

On the map, a card "PAGE · Stale files · by demo-agent · at ops · 60 min left" appears in the top right and a toast
says `demo-agent made a page: Stale files` with an Open button. Nothing opens by itself. When you press Open (or the
card, or Enter on it) the page opens in a panel over the map with its title, its author and when it expires, and
the other live pages as tabs to switch between; Esc or the close button shuts it (Esc, while the focus is not inside
the page). Calling `page` again with the same `id` replaces it in place, and an open panel loads the new version:
that is how an agent keeps a live dashboard (each version has its own revision, so even two in one second
show). A page that is open when it is cleared or runs out loses its frame at once, and the panel says it
expired. On a phone the card shows and says to open it on the desktop; the phone is never sent the page.

A page is the agent's own HTML: at most 262 144 bytes of UTF-8, with a title of at most 40 characters, up to 4 live
at a time (a new page retires the oldest page), each for 30 seconds to 24 hours (default 30 minutes). It is a
picture, not a control surface:

- It is the document of `<iframe sandbox="allow-scripts" referrerpolicy="no-referrer" src="/show/page/frame?…">`: scripts
  run, but the page has an opaque origin, no cookies or storage, no forms, popups, top navigation, modals or
  downloads, and no way to reach the map, the bridge or your session. The map listens for no message from it either.
- The bridge serves that document with headers of its own, so none of the map's policy is inherited: a
  `Content-Security-Policy` (`sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src
  'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; frame-src 'none';
  form-action 'none'; base-uri 'none'; frame-ancestors 'self'`) and `Connection-Allowlist: ()`, which lets the
  document open no connection at all. Everything it needs is **inline**: JS in `<script>` blocks (and `on...`
  attributes), CSS in `<style>` blocks, images and fonts as `data:` or `blob:` URIs. A canvas or an inline SVG
  draws fine. Measured in Chrome 155 against a port that records what reaches it: 31 ways a page might reach out
  (fetch, XHR, beacon, WebSocket, a stylesheet, a script, a font, a form, a popup, a link, an iframe, `location`,
  a meta refresh, `<link rel=preconnect>`, `rel=prerender`, ...) and not one arrives. Without the allowlist 7 of them
  did (prerender sends a GET; preconnect and blocked navigations open a bare connection), and a browser that
  does not honour `Connection-Allowlist` (it is Chrome 152+) has only the policy and the sandbox. So the frame
  protects the map from the page, and from other browsers' hints it protects the page's contents less: put in a
  page only what you would put in a URL.
- The bridge keeps the source in memory and serves it to your browser session alone (the agent token is refused,
  and so is a phone). The source is in no list of shows, no `/state`, no log, and does not pass through the map's
  own page: the frame asks the bridge for it by address.

From a shell without MCP: `container-map show page --title T --html-file FILE` ([CLI.md](CLI.md#workflows-you-can-watch-workflow-alias-wf-show-area-set)).

## Troubleshooting

- *The agent says the tool failed with `no running map found`.* The map is not running, or the server looks in
  the wrong place: start it (`container-map serve ROOT`), or add `--root`/`--port`/`--live-file` to the server's
  arguments. `container-map doctor` prints the live file's path.
- *`agent token or UI session required`.* The live file names a map that has since been replaced by another with a
  different token on the same port; the server reads the file again on the next call. If it persists the file is
  stale: restart the map.
- *Nothing shows in the agent's tool list.* Run `container-map mcp` by hand and paste one line,
  `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`: the reply lists 11 tools, and stderr says why if it cannot start.
  A client that ignores `title` and `annotations` still works: they are optional fields of the protocol.
- The protocol versions spoken are 2025-06-18, 2025-03-26 and 2024-11-05 (the client's own is answered when it is one
  of these, else the newest). A batch (a JSON array per line) is answered with an array, in order, with nothing for
  the notifications in it (and nothing at all for a batch of only those), when the session negotiated 2025-03-26, which
  introduced batches, or 2024-11-05; an empty batch, an `initialize` inside one and more than 64 messages are
  errors. 2025-06-18 removed batching, so a session of that version (or one that has not negotiated yet) gets a
  single -32600 error for an array.
