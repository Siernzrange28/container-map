# Agent API

The bridge (`bridge.py`) serves a small HTTP API for agents under `/agent/`. It gives an agent the
map and its cards as data, and lets it hand out, follow up and check threads. Landing work
(merge, set aside, close) is not in it: that stays with the person. An agent that speaks MCP reaches all of it
as tools, with no curl and no token to handle: `container-map mcp` ([MCP.md](MCP.md)).

The map's cards are built in `cards.py`; the thread verbs are in `tasks.py`. Everything below is
what those files do, nothing more.

## Finding the token

The bridge writes a live file on every boot (`--live-file`, env `BRIDGE_LIVE_PATH`; default
`ROOT/.container-map/run/bridge-<port>.json`: one file per root and per port, mode 600 in a
mode-700 folder, replaced on each boot and removed when the bridge stops). Without a root folder,
or when that folder cannot be written, it falls back to `.bridge-live` next to `bridge.py` (or, if
that is read-only too, `$XDG_RUNTIME_DIR/container-map/bridge-<port>.json`), and a `.bridge-live`
that already exists there is kept current on every start, unless another running map owns it; it
stays, stale, when the bridge stops, so check `pid` before you trust it. The file preview never
serves it. It is one line of JSON:

```json
{"version":1,"boot_id":"…","pid":1234,"port":7428,"agent_token":"…","control_url":"http://127.0.0.1:7428/control"}
```

Send the token as `Authorization: Bearer <agent_token>`. Every card also names this file in its
`auth.token_file` and the base address in `auth.base_url`; the token itself is never in a card.
Requests must come to the loopback host on the bridge's port. If an `Origin` header is sent it must
be the bridge's own origin (curl sends none).

`python3 gamectl.py` finds the file for you (from `--root`/`--port`, `$CONTAINER_ROOT`/`$BRIDGE_PORT`,
or the map above the current folder; `container-map doctor` prints the same path). Setup for the
examples below (needs `jq`; point `LIVE` at your root's file):

```sh
LIVE=~/work/.container-map/run/bridge-7428.json
TOKEN=$(jq -r .agent_token "$LIVE"); BASE=http://127.0.0.1:$(jq -r .port "$LIVE")
```

## GET routes

Query parameters are checked strictly; an unexpected one is a 400. The page's UI session can read
these routes too (the four `/agent/*` ones and `/file`); the POST routes are token-only.

### `GET /agent/map`

No query parameters. The whole map: planets with their problems, the threads (up to 40), the
agents offered, and the calls an agent can make. Built from the last world the page rendered.

```sh
curl -s -H "Authorization: Bearer $TOKEN" $BASE/agent/map
```

```json
{"name": "…", "root": "…", "built": "…",
 "planets": [{"uid": "area:notes", "name": "notes", "path": "/home/you/projects/notes", "purpose": "…", "kind": "…",
              "files": 1234, "size": "…", "last_write_days": 0, "problems": 2,
              "problem_list": ["cron line … has no fresh receipt: …"], "agents_working": 0,
              "trend": "+2 stale cron lines"}],
 "threads": [{"id": "…", "title": "…", "status": "review", "agent": "…", "model": "…", "target": "…", "check": null}],
 "agents": [{"name": "…", "label": "…", "role": "both", "models": [{"name": "…", "label": "…"}], "default_model": "…"}],
 "decisions_waiting": 0,
 "actions": [{"do": "…", "method": "POST", "path": "/agent/task", "body": {"text": "…", "target": "…", "runner": "…"}}],
 "auth": {"header": "Authorization: Bearer <agent_token>", "token_file": "…/.container-map/run/bridge-7428.json", "base_url": "http://127.0.0.1:7428",
          "note": "merging, setting aside and closing threads stay with the person"}}
```

On the map, `planets[].trend` is a string: the sentence from the week-over-week record
(`"+2 stale cron lines"`, `"recovering"`, …), or `""` when the record has nothing to say. A
planet's own card carries the whole record under the same name (below).

### `GET /agent/unit?uid=<uid>`

Exactly one `uid` parameter, `area:<name>` (a planet) or `dir:<path>` (a folder); take them from
`planets[].uid` in the map. The map lists planet uids only; folder uids come from a planet's card:
`GET /agent/unit?uid=area:<name>` → `inside[].uid` (a folder's own card lists its children without
uids). 404 if nothing on the map has that uid.

```sh
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/agent/unit?uid=area:notes"
```

```json
{"uid": "area:notes", "kind": "planet", "name": "notes", "path": "/home/you/projects/notes", "purpose": "…", "area_kind": "…",
 "files": 1234, "size": "…", "last_write_days": 0, "life": "…", "instructions_file": null,
 "worker": null, "git": {"branch": "main", "dirty": 0}, "seats_here": [{"uid": "…", "name": "…", "status": "WORKING"}],
 "threads_here": [{"id": "…", "title": "…", "status": "review", "agent": "…", "model": "…", "check": null}],
 "problems": [{"kind": "cron", "uid": "…", "what": "…"}],
 "trend": {"dstale": 2, "dtrouble": 0, "dage": 0, "dfailed": 0, "span_days": 7.0, "worse": true, "better": false,
           "why": "+2 stale cron lines"},
 "inside": [{"uid": "…", "name": "…", "path": "…", "files": 10, "last_write_days": 3}],
 "top_files": [{"name": "…", "path": "…", "size": 0, "last_write_days": 0}],
 "crons": [{"name": "…", "schedule": "…", "verdict": "…"}],
 "commands": {"open_a_seat_here": "bash /path/to/container-map/door.sh notes",
              "read_a_file": "GET /file?path=<absolute path inside this folder> (same Authorization header; first 64 KB of a text file, no credentials)"},
 "actions": [ … same list as the map … ],
 "agents": [ … ],
 "auth": { … },
 "brief": "# notes (planet) — /home/you/projects/notes\n…"}
```

For a folder, `kind` is `"folder"`, `inside` lists `{name, files, is_folder}`, and `planet` names
its planet; `top_files` and `crons` appear only on planets. `brief` is the card as text to paste
into any agent. `problems[].kind` is `cron`, `board` or `thread`.

`trend` here is the whole record for a planet: `dstale`, `dtrouble`, `dage`, `dfailed` (changes
since `span_days` days ago), `worse`, `better`, and `why`, the sentence the map carries. It is
`null` for a folder and for a planet with no week of history yet.

`commands.open_a_seat_here` is a shell command for the agent's person to run, not one the bridge
runs. Its parts are shell-quoted: a planet named `My Notes` comes out as `'My Notes'`.
`commands.read_a_file` is the `GET /file` call below.

### `GET /file?path=<absolute path>`

One `path` parameter. Read-only, for the agent token or the page's UI session, with the same
checks for both: the path must be absolute and, once symlinks and `..` are resolved, inside the
work root, an extra area, a planet or a `file_roots` folder; a path with a credential-like part
(`.env*`, `.ssh`, `.git`, `.kube`, `.docker`, `.aws`, `*.pem`, `*.key`, …) is never served. Names are
compared in lower case, so `.SSH/id` is refused as `.ssh/id` is.

```sh
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/file?path=/home/you/projects/notes/README.md"
```

```json
{"path": "/home/you/projects/notes/README.md", "size": 2048, "binary": false, "truncated": false,
 "mtime": "2026-10-09T01:02:03+00:00", "text": "…"}
```

`text` is the first 64 KB; `truncated` says there is more. A binary file comes back with
`binary: true` and an empty `text`. Errors: 400 (not absolute, or not a regular file), 403 (outside
the roots, or never served), 404 (no such file).

### `GET /agent/tasks`

No query parameters. Every open thread and the newest 200 closed ones (the limit is for closed
history only; an old open thread is never cut off), newest first, plus the agents on offer. Reading it also lets the
oracle loop send a failed thread with retries left back to its agent (the bridge's own clock does
the same every `tick_seconds`, see `POST /agent/task`).

```sh
curl -s -H "Authorization: Bearer $TOKEN" $BASE/agent/tasks
```

```json
{"tasks": [{"id": "20261009-010352-write-docs-58402e", "title": "…", "text": "…", "runner": "…", "model": "…",
            "target": "/home/you/projects/…", "cwd": "…", "mode": "worktree", "branch": "…", "base_branch": "main", "repo": "…",
            "created": "…", "verify_cmd": null, "retries": 0, "setup_cmd": null, "note": null, "status": "review", "rc": 0,
            "runs": 1, "started": "…", "ended": "…", "check": null, "last_verdict": null, "verify": null,
            "attempts": 1, "setup": null, "retry_due": false, "blocked": null, "merge_blockers": [],
            "evidence": "none"}],
 "runners": [{"name": "…", "desc": "…", "detected": true, "followup": true, "max_minutes": 30,
              "models": [{"name": "…", "label": "…", "desc": "…"}], "default_model": "…", "role": "both", "label": "…"}],
 "max_running": 4}
```

`blocked` is the reason a verify retry that is due can never go out (the thread's agent is not
offered any more, or its worktree is gone), else `null`; while it is set `retry_due` is false and
the thread stays `failed`. A reply (`POST /agent/task/followup`) is refused with 409 for those same
two reasons, so it does not clear the block while the cause stands: offer the agent again (or put
the worktree back) first, and then a reply starts the next run.

`merge_blockers` lists what the evidence says against landing a thread (a failed verify, or one
that `timed out after N s`, a fresh `FAIL` verdict from a check, a failed run or setup (a setup that
`timed out after N s` says so; an agent run the time cap ended reads `the agent's run was killed at
its time cap (30 min)`), a last run that was stopped or lost: with no verify line
`the agent's run was stopped before it finished` or `the agent's run was lost (its supervisor died); …`,
and with a verify line that never ran, the single `verify never ran`,
`the work changed after verify`, `the work changed after check`, and, when the verify or check could
not fingerprint the work (`the work could not be fingerprinted at verify, …`) or the worktree
cannot be read now (`the work cannot be compared with its evidence: the worktree folder is gone`),
a blocker of its own: evidence that cannot be set against the content proves nothing about it.
With the config's `merge_requires_check` on, `no independent check` while no check run has a fresh `PASS`);
the page asks for a second click while it is not empty. An agent cannot land a thread either way.
A check run the time cap ended has the verdict `no verdict · timed out after 10 min`, whatever it
printed or exited with on the way out; it never counts as a `PASS` or a `FAIL`.

`check` is the newest check run. `last_verdict` (`{n, runner, model, verdict, at, stale}` or `null`) is
the newest check that finished with `PASS` or `FAIL`, the one the merge gate holds: a newer check
that crashed, was stopped or got lost does not replace it, and `stale` says work came after it.
A verdict is the last line of the checker's final answer that starts with `VERDICT:`, after at most
markdown (`## `, `- `, `* `, `> `, `_`, `**`; a quote of it inside a sentence, an echoed instruction or a
tool's output never counts). The checker's log is read as an event stream (Claude Code `stream-json`, Codex
`--json`, Gemini) only when its runner's command line asks for one (`--json`, `stream-json`) or its first
line is an event; a plain-text checker that prints a line of JSON among its prose is read as plain text. `the work changed after verify` means the worktree's content differs from
the digest recorded in the verify receipt, `the work changed after check` that it differs from the digest
in the exit receipt of the check whose verdict is in force; each is compared with the worktree on its
own and they need not equal each other. Files git ignores and untracked files named by `merge_skip` do
not count.

`evidence` says how far the work was proved: `none`, `verified` (the newest run's verify line
exited 0), `checked` (a check run ended in a fresh `PASS`, none of the work came after it) or
`verified and checked`. A passing verify is the thread's own test, not proof of correctness: an
agent can satisfy a test without doing the work. Ask for a check (`POST /agent/task/check`)
before relying on it.

A thread's worktree is not in its receipts folder: it is `<worktrees_dir>/<id>` (default
`<tasks_dir>-worktrees/<id>`, see `worktrees_dir` in [CONFIG.md](CONFIG.md)); `cwd` is where the
agent runs. Closing a thread (page or CLI) leaves untracked build junk named by `merge_skip` out of
the commit, and the `merged`/`discarded` receipt lists it under `skipped`.

### `GET /agent/task?id=<id>[&run=<n>][&since=<offset>]`

`id` is required; `run` (a run number, default the latest) and `since` (byte offset into that
run's log, default 0) are optional non-negative integers written with the digits 0-9. Returns the
thread summary (as in `/agent/tasks`) plus the log.

```sh
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/agent/task?id=$ID"
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/agent/task?id=$ID&run=1&since=4096"   # poll for more
```

```json
{"id": "…", "status": "running", "…": "the summary fields above",
 "text": "the full task text", "run": 1, "offset": 4096, "size": 5120, "more": true,
 "lines": [ … parsed log lines … ],
 "prompts": [{"n": 1, "text": "…", "at": "…", "runner": "…", "model": "…", "kind": "task",
              "followup": false, "auto": false, "rc": 0, "ended": "…"}],
 "verify_tail": null, "setup_tail": null, "closed_note": null}
```

Pass the returned `offset` as `since` while `more` is true or the status is `running`.

## POST routes

Send `Content-Type: application/json` and a JSON object (up to 24 576 bytes; up to 1 MiB for a show
whose page op validates); no query string.
Extra or missing keys are a 400. The response is the thread summary, as in `/agent/tasks`.

### `POST /agent/task`

Hand a thread to an agent. Body: `{text, target, runner[, model, isolate, verify, retries, setup]}`.
`verify`, `retries` and `setup` are refused with a 400 unless `agent_verify` is `true` in
`container.json` (see "What is refused").

- `text`: plain text, at most 8000 characters.
- `target`: an absolute folder path inside the roots the map shows (the work root, the extra areas
  and the planets; `file_roots` is preview-only and does not count). A file path is cut to its
  folder; a folder that does not exist is a 404.
- `runner`: one of `agents[].name`, not one with `role: "check"`. A runner's name is lowercase
  letters, digits and hyphens, at most 32 characters.
- `model`: optional, one of that agent's `models`.
- `isolate` is accepted by the bridge but an agent's value is ignored: it always asks for
  isolation. A thread gets its own git worktree (branch `cm/<id>`, cut from the last commit) only
  when the target is inside a git repo that has at least one commit and sits within the map's roots.
  Otherwise (no repo, a repo above the roots, or a repo with no commits) the agent works in place in
  the target folder, and the diff is the list of files changed after the task started.
- `verify` (needs `agent_verify`): optional, one shell line (at most 400 characters, no control
  characters) that must exit 0 in the thread's folder. The supervisor (`tasks.py`) runs it after
  every run of the agent that was not a stop and not a kill by the time cap, whatever the agent's
  own exit code was: a passing verify makes the thread `review` even when the agent exited
  non-zero (the run log says so), a failing one makes it `failed`. It does not run when the thread
  is stopped or when the time cap kills the agent.
- `retries` (needs `agent_verify`): optional, 0 to 3; needs `verify`. A thread whose verify failed,
  with retries left, goes back to its agent with the failure as an automatic follow-up. That
  happens on the bridge's own clock, every `tick_seconds` (default 10; with or without a page
  open; `0` turns the clock off) and whenever the thread list is read.
- `setup` (needs `agent_verify`): optional, one shell line under the same limits, run in the new
  worktree before the agent's first run (on a follow-up only until one setup has passed, so a
  setup that failed or timed out runs again before the agent, and never again once it has passed;
  never on a check). It only applies to a
  thread that gets its own worktree; for an in-place thread it is skipped. A non-zero exit ends the run with exit code 125 before any agent starts. Without a
  `setup`, the config's `worktree_setup` line for the repo (or `"*"`) is used, if there is one.

```sh
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" $BASE/agent/task \
  -d '{"text":"Add a test for X, then make it pass.","target":"/home/you/projects/app","runner":"<agent name>"}'
```

With `"agent_verify": true` the same call can carry the oracle loop:

```sh
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" $BASE/agent/task \
  -d '{"text":"Add a test for X, then make it pass.","target":"/home/you/projects/app","runner":"<agent name>","verify":"python3 -m unittest -q","retries":2}'
```

### `POST /agent/task/followup`

Send a stopped thread's agent back to work. Body: `{id, text}`. 409 while the agent is still
working, when something of a lost agent still runs, when the thread is closed, when its worktree
is gone, or when its agent is not offered any more (the same two reasons that set `blocked`).
When the previous run was ended by the time cap, the prompt says so.

```sh
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" $BASE/agent/task/followup \
  -d '{"id":"'$ID'","text":"Also cover the empty case."}'
```

### `POST /agent/task/check`

Have another agent read the work: read-only, ends in a line `VERDICT: PASS` or `FAIL` (only a line
of its own in the final answer counts, markdown before it allowed). Body:
`{id, runner[, model, text]}`; `runner` must be an agent whose role is not `work`. Same 409 cases
as follow-up, plus 409 when nothing has run yet. Every verb also answers 409 when the thread's record
was altered after it started (repo, branch, base, verify, setup and retries are pinned at creation).
A stopped check's verdict reads `stopping`, then `stopped`.

```sh
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" $BASE/agent/task/check \
  -d '{"id":"'$ID'","runner":"<a different agent>"}'
```

## Workflows you can watch, and shows

A workflow is a few steps, each where it works: a folder on the map (`at` an absolute path inside the
map's roots; the map draws the step at that planet or sub-folder), another machine (`machine:<name>`),
a named place that has no folder (`place:docs site`, `place:the inbox`: a small beacon, up to 24
characters), or this machine (no `at`). A step is `note` (you do the work and report it), `task`
(the map hands it to an agent as a thread: its own worktree, the test that must pass, retries, a
second opinion, the person's merge) or `gate` (the person approves). The map shows each step in its
place with its state, what it says and what it made, live. The engine is `workflows.py`; the store
sits beside the threads (`workflows_dir`, default `ROOT/.container-map/workflows`), so the bridge and
`container-map wf` work on the same workflows. `container-map wf schema --json` (or `workflows.schema()`)
prints the whole spec, the show ops and every bound.

### `POST /agent/workflow`

Body: the spec, up to 24 KB: `{title (<= 60), goal (<= 140), by: {name, seat}, start: "now"|"plan",
stale_after_min, steps: [{key ([a-z0-9-]{1,24}), label (<= 60), at, kind, after: [keys], inputs,
task: {text, runner, model, verify, retries, setup, check_runner, wait}}]}`, at most 24 steps, each
waiting on at most 8. Steps that wait on each other in a circle, or on a key that does not exist, are a
400 that names them. Answers **201** with `{id, token, start, notes, workflow}`: the `token` is shown
this once (only its sha256 is kept) and goes in `X-Workflow-Token` on every update. A spec whose task
steps carry `verify`, `setup` or `retries` waits for the person's Start (`start` comes back `plan`)
unless `agent_verify` is on; a `check_runner` of the runner's own model family is a 400 ("a second
opinion must come from a different model family"). A workflow made from inside a workflow's own task
sends `X-Workflow-Depth` (its `CM_WORKFLOW_DEPTH`); past depth 2 it is refused. Limits: one new
workflow every 2 seconds, 16 open at once, 200 a day (429, or 507 when the folder is full: `wf retire`
moves old ones to an attic folder); task launches from agent-made workflows stop at
`workflow_launches_per_day` (default 40) a day, after which a ready task step waits ("today's agent
launch limit (40) is used up; it starts tomorrow or when you press Retry").

```sh
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" $BASE/agent/workflow \
  -d '{"title":"Tidy the notes","by":{"name":"my agent"},"steps":[{"key":"read","label":"Read them","at":"/home/you/projects/notes","kind":"note"},{"key":"ok","label":"Look at the list","kind":"gate","after":["read"]}]}'
```

### `POST /agent/workflow/step`, `/pulse`, `/done`, `/stop`

Each needs the bearer token and the workflow's `X-Workflow-Token` (403 without it).
`step {id, key[, state, says, progress, output]}` reports a note step: `state` is `working`, `done`,
`failed` or `skipped`; `says` a sentence (<= 200); `progress` `120/800`; `output` an absolute path of a
regular file on the map (kept as a snapshot when the step is done; a hidden or secret-shaped name, a
link, a folder of credentials or of git's own are refused) or a few words. A step is reported only once
what it waits on is through (409), a gate only by the person (403), a task step's state only by its
thread (403; its own agent gets a token for its step's `says`/`progress`/`output` in
`CM_WORKFLOW_TOKEN`). The same state again within 10 seconds, or faster than 4 a second, only updates
what the map shows (`{"coalesced": true}`); past 8 MB of updates a workflow answers 413 ("start a new
one"), but a step's finishing move is always taken. `pulse {id[, key]}` says you are still at it
(never 429: over the rate it is dropped with `{"coalesced": true}`); a working workflow with no news for
`stale_after_min` (15) reads "no news". `done {id}` ends it (steps that never started read skipped; a
gate still waits). `stop {id[, why]}` launches nothing more.

### `GET /workflows[?since=<ISO time>]`, `GET /workflow?id=`, `GET /workflow/replay?id=[&page=N]`

For the bearer token or the page's session. `/workflows` lists the open workflows and the newest 50
closed ones (only those changed after `since`), as summaries (`{id, title, goal, by, state, updated,
done, total, elapsed_s, current, sentence, steps: [{key, label, kind, state, planet_uid, moon_uid,
place, after, says, progress, output, artifact, tid, attempt, why}]}`; places by name, never paths),
plus the live shows and `now`. `/workflow` adds the last 50 events, each step's `at`, the kept outputs
and what Start would run as you (`shell_lines`). `/workflow/replay` gives the frames (at most 2,000 a
page) a replay folds into the final summary; each frame names the planet and who moved it. The same
`workflows` and `shows` ride on `GET /agent/tasks` (and the page's `/tasks`). A paired phone reads all
three with every path and every branch name taken out.

### `POST /agent/show`, `POST /agent/show/clear`

A show paints on the map for a while (memory only, 30 s to 15 min, default 5 min; at most 8 live, a
new one retires the oldest). It never starts, stops or changes work, holds no links or buttons, and
every rendering of it names its author. Body: `{ops: [...], at, by (<= 24, default "an agent"),
ttl, id}`; `at` (a uid such as `area:notes` or `dir:notes/old`, a `place:<name>`, or a folder path,
which the bridge turns into its uid) is where the ops are drawn unless an op says its own `at`; `id`
replaces the show with that id (a live progress card). At most 16 ops:

- `label {text <= 80}` · `marker {icon: flag|pin|star|warn, text <= 40}` · `ring {state, progress}`
- `beam {from, to}` · `route {stops: 2-8 anchors}` · `radio {from <= 24, text <= 80}`
- `fx {preset: sparkle|ring|burst|text, color: #RRGGBB or a step state, count 1-64, life 0.2-3 s,
  text <= 24 (preset text)}`
- `card {title <= 40, rows: <= 8 of [key <= 20, value <= 40], text <= 200, bar: [a, b] with
  0 <= a <= b <= 10^6}` · `chart {label <= 40, values: <= 64 finite numbers, unit <= 8, kind: spark|bars}`
- `page {title <= 40, html}`: your own HTML page, which the person opens in a sandboxed panel (below)
- any op: `xray: true` asks the 3D view to draw it through planets.

An unknown op, or one with a bad field, is skipped and the rest are kept: the reply lists it in
`skipped` (and why in `errors`). A value out of range is clamped and named in `clamped` (`count: 999`
is kept as 64, nine card rows as eight; a number past what a float holds is cut the same way). Text with
control or direction characters, an author (`by`) that is not text, and chart values that are not finite
numbers ("chart values must be finite numbers"), are a 400 for the whole show.
Reply: `{ok, id, expires, skipped, clamped, errors, show}`. `clear {id}` takes one down early. Every
show the map takes has a `rev`, a number that only grows (one counter for all shows, started from the clock, so
it also grows across a restart); a show put again under the same id has a new one. The page reloads an open
page when its `rev` changes, which is why two versions put in the same second with the same size still show.

#### The `page` op

`{op: "page", title (<= 40), html}`: a whole HTML page of your own (a dashboard, a chart, a bug
visualizer) that the person opens inside the map, without leaving it. It is a show like the others:
it names its author, it never starts, stops or changes work, and the bridge keeps it in memory only.

- **Size and text.** `html` is at most 262 144 bytes of UTF-8, counted in bytes; a page over it is
  never cut (see the body limit below: it is a 413 that names its size). A control character other than
  newline, return and tab is a 400 for the show. One page op to a show; with no `at` (on the op or the
  show) it sits at `place:pages`. A body over 24 576 bytes is taken only when a page op in it validates
  (JSON escapes make a 256 KiB page larger, up to 1 MiB); that is decided before anything is stored, and
  otherwise it is a 413 that says why the page did not count (`request body is too large; page: html is
  262145 bytes; ...`).
- **Lifetime and count.** A show with a page op lives 30 s to 24 h, default 30 min (`ttl` in seconds).
  At most 4 pages are live, counted within the 8 live shows: a new page retires the oldest page
  (`retired` in the reply names it); a page with the id of a live one replaces it. If the page op is
  skipped, the rest of the show lives by the other shows' bounds. A page that is open when it is cleared or
  runs out loses its frame at once (its script stops with it) and the panel says it expired.
- **Where the source goes.** `/state`, `/tasks`, `/agent/tasks`, `/workflows` and the show's own reply
  carry a page's metadata only: its `title` and `bytes` in the op, `by`, `at`, `rev` and `expires` on the
  show. The source is the document of the panel's frame: `GET /show/page/frame?id=<show id>&rev=<rev>`
  answers it as `text/html`, to the page's own UI session only (the agent token is a 403: a page is for
  the person's screen; a phone gets a 404 and sees the card with "open this on the desktop"; an unknown or
  expired id is a 404; the query is exactly an id and a revision number). `rev` is the version the panel
  asked for, and makes a new version a new address; the version that is live now is what comes back. There
  is no JSON route for the source any more.
- **What the panel does with it.** A page appears as a card on the map and a toast; the person
  clicks it to open the panel (it never opens by itself). The panel's frame is
  `<iframe sandbox="allow-scripts" referrerpolicy="no-referrer" src="/show/page/frame?id=…&rev=…">`:
  no `allow-same-origin`, forms, popups, top navigation, modals or downloads, so the page has an opaque
  origin and no access to the map, the bridge or the person's cookie, and the map listens for no message
  from it. The session cookie does reach the frame's navigation (it is a same-site request from the map;
  measured, and it is what lets only the person's session be served), and no secret is in the address. The
  frame document comes with headers of its own, so nothing of the map's policy is inherited:
  - `Content-Security-Policy: sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline';
    style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src
    'none'; frame-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'` (the page is
    sandboxed even if it were opened on its own, and only the map may frame it);
  - `Connection-Allowlist: ()`: the document may open no connection at all;
  - `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`,
    `Cross-Origin-Resource-Policy: same-origin`.

  So **everything the page needs is inline**: scripts and styles in `<script>` and `<style>` blocks
  (`on...` attributes work too), images and fonts as `data:` or `blob:` URIs. It cannot fetch, load a
  script, stylesheet, font, image or frame, submit a form, open a window or follow a link out.
  **Measured, in Chrome 155** (a real frame, aimed at a port that listens and records what reaches it): 31
  ways to reach it (fetch, XHR, beacon, WebSocket, EventSource, img, stylesheet, script, `import()`,
  Worker, font, video, audio, object, embed, SVG image, CSS `url()`, `<base>`, prefetch, DNS prefetch,
  ping, form, `window.open`, links, an iframe, `location`, a meta refresh, `<link rel=preconnect>` and
  `rel=prerender`): 0 arrive with the allowlist. With the policy alone 7 of them did (a GET for prerender, a
  bare connection for preconnect and for the navigations the policy then blocks); the allowlist is what
  closes those. Other browsers may not honour `Connection-Allowlist` (it is Chrome 152+): there the page is
  held by the policy and the sandbox, and the hints above can still leave. The map's own page sends no
  allowlist, so its links out (`open in T3`) and `window.open` of a seat's address work (measured the same
  way), and its policy lets it frame exactly this route and no other path (`frame-src` names the path).

```sh
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" $BASE/agent/show \
  -d '{"by":"my agent","at":"area:notes","ttl":3600,"ops":[{"op":"page","title":"Stale files",
       "html":"<!doctype html><h1>12 stale files</h1><script>document.body.append(\" and counting\")</script>"}]}'
```

```sh
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" $BASE/agent/show \
  -d '{"by":"my agent","at":"area:notes","ops":[{"op":"card","title":"Tidy-up","rows":[["files","120"]],"bar":[3,9]},{"op":"fx","preset":"sparkle"}]}'
```

### `POST /control`: point, focus, follow

Three more actions for the control queue (the bearer token; `request_id` is any name of 1-128 letters,
digits and `._:-`): `{"type":"point","target":"area:site","text":"<= 80","seconds":20}` (an arrow
at a uid or place, its caption in the status bar, 5-120 s, at most 8 up), `{"type":"focus",
"target":"area:ops"}` (centre a planet) and `{"type":"follow","workflow":"w-..."}` (select a workflow
and keep it in view). Each may carry `"by":"Claude Code"` (one plain line, <= 24; the queued action holds
"an agent" when none is given) so the page can name who is showing the person something. A target that is
not on the map, or a workflow that is not there, is a 400. Sending a `request_id` again with the same
action answers the first reply (200), even when eight pointers are up; a new one then gets the 429.
Focus and follow move the person's view only as their setting "Agents can move my view" allows
(Off / When idle / Always); otherwise the page asks.

### The person's routes

`POST /workflow/start`, `/workflow/gate {id, key, pass[, why]}`, `/workflow/retry`, `/workflow/skip`,
`/workflow/stop`, `/workflow/new` (a template: `{template, at, goal[, runner]}`, or Run again:
`{from}`) and `POST /area/set {uid, kind[, glyph]}` (what a planet holds: written to `areas.json`, the
bytes it replaces kept as `areas.json.prev`; the `.prev` before moves to `.prev.1`, `.prev.2`) are buttons on the page: the session cookie, the exact Origin and
browser Fetch Metadata, never the bearer token (403). A paired phone may answer a gate
(`/workflow/gate`); every other one of these is a 404 there.

### From the shell: `container-map wf` and `container-map show`

The same calls without curl (docs/CLI.md has every option): `wf new SPEC.json|-` prints the id and the
token, or writes both to a mode-600 file with `--token-file PATH`; `wf step KEY --state ...`,
`wf pulse`, `wf done`, `wf show [--wait]` take the id and token from `$CM_WORKFLOW` /
`$CM_WORKFLOW_TOKEN` or that file. `wf wrap -- CMD` runs a command as a one-step workflow.
`container-map show card|chart|page|put|clear` and `show PATH "caption"` go through the running map with the
token from its live file. `container-map mcp` serves the same routes as MCP tools ([MCP.md](MCP.md)).

### Example: a Claude Code session

Claude Code's Bash tool keeps no environment between calls, so the session keeps its workflow id and
token in a mode-600 file instead of `$CM_WORKFLOW`; every line below is one Bash call and exits 0.
Note steps mean the agent does the work itself and reports it; nothing here launches another agent, so
no Start is needed. The `place:` step shows work that has no folder. (Run against a running map;
`/home/you/projects/app` stands for a folder on it.)

```sh
container-map wf schema --json > /dev/null                      # 1. discover the spec and show-op schema
container-map wf new --token-file .cm-wf --json - <<'EOF'        # 2. say what this session will do
{"title":"Fix the phone labels","goal":"Every planet name sits under its body on a 390 px screen",
 "by":{"name":"Claude Code"},"steps":[
  {"key":"read","label":"Read the label code","at":"/home/you/projects/app","kind":"note"},
  {"key":"fix","label":"Fix the slots","at":"/home/you/projects/app","kind":"note","after":["read"]},
  {"key":"test","label":"Run the tests","at":"/home/you/projects/app","kind":"note","after":["fix"]},
  {"key":"site","label":"Check the docs page","at":"place:docs site","kind":"note","after":["test"]},
  {"key":"ok","label":"Look at the phone shot","kind":"gate","after":["site"]}]}
EOF
container-map wf step --token-file .cm-wf read --state working --says "reading ui/labels.js"
container-map wf step --token-file .cm-wf read --state done
container-map wf step --token-file .cm-wf fix --state working --progress 1/3
container-map show chart --by "Claude Code" --at /home/you/projects/app --label "label overlaps" --values 9,4,0
container-map wf step --token-file .cm-wf fix --state done --output /home/you/projects/app/ui/labels.js
container-map wf step --token-file .cm-wf test --state working --progress 120/800
container-map wf step --token-file .cm-wf test --state done --says "812 passed"
container-map wf step --token-file .cm-wf site --state done --says "docs page shows the new labels"
container-map wf show --token-file .cm-wf --wait                # 3. returns when the owner approves or rejects the gate
```

The person approves the gate from the map (or the phone, or `container-map wf gate ID ok --pass` in their own
terminal) and the last call returns. `test_workflows_api.py` runs this block as it is written here.

### The Claude Code hook

`settings.json` is yours to edit (paste this into its `"hooks"`): after every tool call the session
says it is still at work, so its workflow never reads "no news" while it works. The pulse exits 0
silently when there is no token file or no map, so it never fails a session.

```json
{"hooks": {"PostToolUse": [{"matcher": "*", "hooks": [{"type": "command",
  "command": "container-map wf pulse --token-file \"$CLAUDE_PROJECT_DIR/.cm-wf\""}]}]}}
```

## What is refused, and why

- **Merge, set aside, stop and close are human-only.** There is no `/agent/` route for them. The
  matching `/task/…` routes need the page's same-origin UI session, and `/task/close` additionally
  checks browser Fetch Metadata (`Sec-Fetch-Site: same-origin`): "landing a thread is for the page
  in your browser; agents use /agent/* and leave merging to you". The token cannot reach them (403).
  Renaming a thread (`POST /task/rename`) and the page's meta board (`/meta`, `/meta/ask`,
  `/meta/answer`) are the same: the page's own session, no agent route, and a 404 on the phone listener.
- **What that guards, and what it does not.** These gates stop a page from another origin and
  anything that holds only the agent token. They do not stop a process running as your own user:
  such a process can fetch `GET /` and gets the same session cookie as your browser, and a script
  can send Fetch Metadata headers by hand (it could equally read the live file or run `git`
  itself). One OS user cannot be fenced from itself, so "human-only" means "not through the agent
  token, and not from another website".
- **The phone listener is not the agent API.** `/agent/*` is a 404 on the second listener that
  `remote` opens, and a pairing code is made only by the page's UI session over loopback (`POST
  /phone/pair`): the agent token is a 403 there, so an agent cannot pair itself a phone and land
  threads from it. As with the rule above, a process running as your own user can still fetch `GET /`
  and get that session.
- **Agents cannot turn isolation off.** `isolate` from an agent is overridden to true; whether that yields a worktree depends on the target (see `POST /agent/task`).
- **`verify`, `retries` and `setup` run a shell line as you.** They are the strongest thing an
  agent can set, so they are off for agents: unless `container.json` has `"agent_verify": true`
  (default `false`), a `POST /agent/task` that carries a non-empty `verify`, a non-zero `retries`
  or a non-empty `setup` is a 400 (`verify is set from the page; agent_verify in container.json
  enables it for agents`; for `setup`: `setup is set from the page; …`). An empty `verify`, `retries: 0` or an
  empty `setup` is the same as leaving it out. The page's own `POST /task` is never gated, and the
  card leaves the three fields out of its example body while the gate is closed. With the gate
  open the limits are: one line of at most 400 characters without control characters, `retries ≤ 3`,
  and `retries` needs `verify`.
- **Limits:** a new thread at most every ~2 seconds (429) and 4 running at once (429); targets
  outside the shown roots, or inside the task store, are 403. The 4 is counted across everything
  that shares the task store (this map and any `cli.py thread` on the same store), under a store
  lock held from the check until the run is recorded, and a lost thread whose agent still runs
  counts until it is stopped. A request that waits over 10 minutes for the store or a repository
  lock (another process is mid-merge) is a 503.
- **Workflows: no agent route starts one, passes a gate or accepts changes.** A workflow an agent makes
  runs its note steps as the agent reports them and its task steps as threads (with the rules above);
  a planned one waits for the person's Start, a gate for the person, and merging stays theirs. Text an
  agent writes in a workflow or a show is drawn as text (never markup, never a link) and quoted to the
  assistant as data, never as instructions.
- **Audit:** the POST routes (`/agent/task`, `/agent/task/followup`, `/agent/task/check`) write an audit row with `"via": "agent"`; so do a workflow made (`workflow-create`), a show (`show`), a pointer, focus or follow, and every one of the person's workflow buttons and `/area/set`; GET reads are not audited.

## Errors

Every failure is an HTTP error status with a JSON body of one key:

```json
{"error": "agent token required"}
```

Statuses you will see: 400 (bad query, body or value, a request line the server cannot parse,
or text that cannot be written as UTF-8 such as a lone surrogate (`"\ud800"`), which is refused before
anything is stored),
403 (`agent token or UI session required` on GETs, `agent token required` on POSTs, or a target
outside the roots), 404 (unknown uid, thread or route, or a `target` folder that does not exist),
405 (any method but GET and POST, with `Allow: GET, POST`; a HEAD answer has no body), 409 (thread
is in the wrong state), 411/413/415 (missing length, body too large, not `application/json`), 421
(wrong `Host`), 429 (too many threads, workflows or shows), 500 (task store failure, or a failure nobody planned for:
the body is `{"error": "internal error"}` and the audit log gets one `http-error` row), 501 (an
HTTP method the server has never heard of), 502 (`git worktree add failed: …` or `the agent could
not be started: …`: the machine could not set the thread up, the request was fine), 503 (the task
store or the repository is locked by another process for over 10 minutes), 507 (the workflows folder is
full: `container-map wf retire --older 30d` moves old ones to an attic folder). `POST /agent/workflow`
answers 201 when it made one.
