# CLI reference

Everything runs as `python3 cli.py <command>` from the tool's folder (or `container-map <command>`
if you put it on your PATH). Python 3.10+, stdlib only.

## The map

| command | what it does | exit |
|---|---|---|
| `render [ROOT] [--out FILE] [--port N]` | write the page once (static, `file://`-openable). A file page shows a banner with a link to the live map at port N (default 7428, or `BRIDGE_PORT`) and the command that starts it there (`container-map serve ROOT --port N`, or `python3 cli.py serve ROOT --port N` from an unzipped copy; no `--port` for 7428); it never probes that address or opens it by itself | the renderer's exit code |
| `serve [ROOT] [--port N] [--lock]` | bridge + 15 s renderer; prints the live URL (with `--lock`, or `"ui_lock": true` in the config, the page opens only through a private link, printed to a terminal and otherwise shown by `link`; see "The lock" below). Ctrl-C (or a SIGTERM/SIGHUP sent to the CLI alone) stops it, and the bridge and renderer with it, with one line, `container-map: stopped` | 0 after a stop, 1 if the port is taken |
| `demo [--no-serve] [--port N] [--static] [--root DIR]` | build a demo workspace, seven folders, two completed tasks and one working agent while served (see below) | 0, 1 it could not be built, 2 usage |
| `config [ROOT]` | print the resolved config | 0 |
| `doctor [ROOT] [--port N]` | check the version, python, git, bash, agents, config, root, port | 0 ready, 1 not ready: a missing piece, a root that does not exist, a `container.json` that cannot be read (with its parse error and line) |
| `init ROOT [--create]` | write `ROOT/.container-map/container.json` (never overwrites); `--create` makes ROOT if it is not there | 0, 1 not a directory, 2 usage |
| `install ROOT [--port N] [--dry-run] [--lock]` | run `serve` (with `--lock`: `serve --lock`) as a user service (systemd or launchd) and wait for its bridge; a locked map's private link is printed when it is up | 0, 1 the port is held by something else, the start failed or no bridge came up, 2 usage or no service manager |
| `link [ROOT] [--port N]` | print the private link of the running locked map (found like the live file below); only the user who started the map can read it | 0, 1 no map is running there, it is not locked, or its file is not private |
| `uninstall [--dry-run] [--force]` | stop that service; its file is renamed, not deleted | 0, 1 stopping failed (the file is kept, so a retry still finds it; `--force` retires it anyway) |
| `pair [--hours N] [--port N] [--json]` | ask the running map for a one-use pairing code (ten minutes, or `--hours N` up to 168) and print the address to open on the phone; needs `remote` in the config | 0, 1 no map answers or phone access is off or refused, 2 usage |
| `devices [--revoke ID] [--port N] [--json]` | list the paired phones, or end one at once | 0, 1 no map answers or no such device, 2 usage |
| `workflow ...` (alias `wf`), `show ...`, `area set ...` | workflows you can watch on the map, an agent's shows, what a planet holds (see below) | 0, 1 refused, 2 usage |
| `mcp [--root R] [--port N] [--live-file F]` | the running map's agent API as MCP tools on stdin and stdout, for any agent that speaks MCP; found like `show` finds the map; logs to stderr; [MCP.md](MCP.md) has the lines that add it to Claude Code and Codex and every tool | 0 when stdin ends, 2 usage |
| `--version` (or `-V`) | print `container-map X.Y.Z` and exit; the number is `VERSION` in `cli.py`, also shown by `doctor` | 0 |

`-h` or `--help` after any command prints that command's help and does nothing else (`serve --help`
does not start a server). A command refuses an option it does not take, exit 2, naming the ones it
does (`render`, `serve` and `config` as well as the rest). Every command except `doctor` and
`thread`, which say it in their own words, stops with exit 1 on a Python below 3.10 before it
does anything; the one exception is `demo --static`, which needs neither git nor 3.10 (it only
renders a page, and runs on any Python from 3.9; below that it stops with exit 1 the same way). On Windows itself every command but `doctor` stops with exit 1 and says to use
WSL2 (see below).

`--config FILE` works with every command that reads a config. Four do not, and refuse it (exit 2,
`unknown option --config`): `demo` takes only its own options and builds its own config, `pair` and
`devices` find the running map by its port (`--port N`, else `$BRIDGE_PORT`, else 7428: a config has
no port to give them), and `uninstall` finds the service by its name. Without it the config is
`ROOT/.container-map/container.json`, else the defaults (details: [CONFIG.md](CONFIG.md)).
Install, step by step: [INSTALL.md](INSTALL.md).

`render`, `serve` and `config` refuse a ROOT that is not a folder (and, with no ROOT, a
`$CONTAINER_ROOT` that is not one); `doctor` reports it instead and exits 1. Looking at a map never
makes its folder: nothing creates a root except `init ROOT --create`.

### doctor

Prints what it found, then `ready.` (exit 0) or `not ready: …` (exit 1) naming everything that is
wrong: a missing piece (python 3.10+, git, bash), a root that does not exist (no fallback to
`~/work`: it says which folder it looked for and how to point at another), a `container.json`
that cannot be read (its path and the parse error with its line and column; the map itself falls
back to the defaults with a warning, doctor does not call that ready). A busy port is reported
with whose it is: **this map's own bridge** (its live file names a running process), or
**something else**, which adds a `note:` line (`serve` and `install` refuse that port; pass
`--port N`). Neither makes the machine "not ready": another service may simply own 7428. The `live file` row is where the bridge
keeps its credentials for this root and port. With no agent CLI on PATH it says so and points at
`container-map demo`.

A config key the file names and the map ignores is printed too, one `ignored` row each with the
reason: a key that is not in the defaults (a typo; keys starting with `_` are comments and are
not listed) or a value of the wrong type (`tick_seconds` as a decimal, a list where text goes).
It does not make the machine "not ready", but the setting is not in force.

### The lock

For a computer other people log in to (README, "Who can open your map"). `serve --lock` (also `install --lock`,
which writes `--lock` into the service command, or `"ui_lock": true` in the config) makes the page open only
through `http://127.0.0.1:PORT/?k=<launch key>`. `serve` makes the key for each start and hands it to the bridge
in its environment (the bridge clears it there); the bridge keeps it in memory and in its live file
(`launch_key`, mode 600). `serve` prints the link only when its output is a terminal, since a service's output
is a log; `install` prints it to its own terminal once the bridge is up, and `link` prints it any time. Without
the key (or the session cookie it sets) `GET /` and `/game.html` answer 403 with a plain "locked" page; the key
in the address is exchanged for the cookie by a 303 redirect to the plain address. `pair` and `devices` read
the key from the live file themselves, so they work on a locked map when run by the same user. `doctor` has a
`ui lock` row: on, off, or, when more than one account with a login shell exists (user id 1000 and up on Linux,
501 and up on macOS) and the lock is off, a warning with how to turn it on (never a failure by itself).

### install and uninstall

`install` refuses a port held by something that is not this map's own bridge (before writing
anything), writes and starts the service, then waits up to 15 s for the new bridge: its live file
(a new boot, not the previous run's) and an HTTP 200 on the port. If that does not happen it prints
why and the service's last log lines (`journalctl --user -u container-map`, or launchd's log file)
and exits 1 instead of "installed and started"; the unit stays, `uninstall` removes it. (A big
root is rendered once before the bridge starts, so a service that missed the 15 s may still come
up a moment later; the message says so and names the log command.)
`uninstall` renames the unit file only after the service was stopped; if stopping failed it keeps
the file, says why and exits 1. A service that was never loaded or no longer exists ("not
loaded", "does not exist") counts as stopped. When the manager cannot be reached at all (no user
bus after a failed `install`), `uninstall --force` prints the failure and retires the file anyway;
the message names the file.

### Which python

`cli.py` may be put on your PATH through a symlink: the folder it works from (`render-game.py`,
`world.sh`, the themes) is the one beside the real file, not beside the link.

`serve`, `render`, `demo` and the service run on the Python the CLI runs on, also in the scripts
they start: the CLI passes it as `CM_PYTHON` to `world.sh` (for `serve` and for a served `demo`),
which uses it for every Python call, and `demo.sh` starts `cli.py demo` on it (running them by
hand falls back to `python3`). A venv install is therefore the Python of the bridge and the
renderer too.

### The bridge's live file

The bridge writes its port and agent token to `ROOT/.container-map/run/bridge-<port>.json`
(mode 600 in a mode-700 folder): one file per root and per port, so two maps on two ports never
overwrite each other, and nothing is written next to the code. `--live-file` and `BRIDGE_LIVE_PATH`
still choose another place. `gamectl.py` finds the file with no flags from `--root`/`--port`,
`$CONTAINER_ROOT`/`$BRIDGE_PORT`, or the map above the current folder (with several bridges set up
and nothing saying which, it asks for `--port N`); `doctor` shows the same path (it reads the
same `BRIDGE_LIVE_PATH`). If the root's `.container-map/run` cannot be written (a read-only mount, a
root-owned checkout) the file goes beside the code, as before, or if that is read-only too into
`$XDG_RUNTIME_DIR/container-map/` (else `~/.cache/container-map/`); `gamectl.py` looks there as
well. A bridge removes its own file when it stops. A legacy `.bridge-live` beside the code that
exists is kept current as well on every start, unless another running map owns it; it is left in
place, stale, when the bridge stops (readers check its `pid`), so the next start refreshes it.

### Git repos

When a root is itself inside a git repo, `init`, the first `render`/`serve`, the first thread and
anything else that makes the `.container-map` folder (the history, the cron cache, the bridge's
`run/` folder) add `.container-map/` to that repo's `.git/info/exclude` (local to your clone;
nothing is committed), so the map's folder is not an untracked change. A `.container-map` that
already existed is left as it is, except `run/`, which holds the agent token: it gets a `.gitignore`
of its own (`*`), so `git add .` and a thread's diff never take the token in.

## Try it with no agent: `demo`

`demo` is for a stranger with no agent CLI and no login. It builds a fresh folder (never an
existing one, never beside the code) and fills it:

- `todo-app`, a git repo with a small module and `test_todo.py`, one test failing;
- `notes-site`, a git repo whose README carries a `TODO:` line, so the board shows one parked
  item;
- `tiny-tools`, a third git repo with a small folder report;
- `notebook`, `handbook`, `sample-data` and `archive`: notes, guides, a tiny table and older drafts;
- `.container-map/container.json`: `detect_runners` off, one runner `demo-agent` ("Demo agent
  (scripted)") whose command is `demo_agent.py` (the prompt on stdin, `--check` for a review),
  an empty crontab file, `processes` and tmux off, the wingmate off, `demo` on.

`demo_agent.py` is a script, not a model. Its first attempt adds `todo.complete()` with an
off-by-one; when the prompt says the verify command failed it fixes the line; `--check` runs the
tests and ends with `VERDICT: PASS` (or FAIL if they fail). A task it does not know exits 1, and
so does any run outside a thread's own worktree: the page can hand the parked `notes-site` item
to any configured runner, and that run starts in the tool's folder, where the script prints why
it will not touch it and changes nothing. Start a thread (**N**, or `thread new`) to use it.

Then, through `thread` (real worktrees, supervisors and receipts), it starts a thread on
`todo-app` with verify `python3 -m unittest -q` and `--retries 2`: the first verify fails, the
oracle loop sends it back, the second passes. A check run (the same script with `--check`)
says PASS and the thread is merged. A second thread, on `notes-site`, ends in review and stays
there for you to merge in the page. When serving, a third task on `tiny-tools` stays running
for up to fifteen minutes, printing progress as a real ship works beside its folder. Ctrl-C stops
that task too. The first browser visit offers a six-step guide; Help replays it. The threads' log
streams to stderr as they run. Building still takes seconds.

| option | meaning |
|---|---|
| `--no-serve` | stop after the threads; print the folder and `serve FOLDER`, which serves it later |
| `--port N` | serve on this port (default: any free port, never your own map's by accident) |
| `--root DIR` | build into DIR, which must not exist or be empty (anything else is refused and left alone) |
| `--static` | the older demo: one generated six-folder workspace rendered to `demo.html`, to open as `file://`; nothing can act on it. No git, no threads |

By default the new folder is `~/.cache/container-map/demo-<date>-<time>-<random>/`
(`$XDG_CACHE_HOME` if set). Served, the bridge's runtime file and log, the page, the thread store
and the task receipts all live in that folder's `.container-map/`; `Ctrl-C` stops the demo and
nothing is deleted, so remove the folder yourself when you are done. `bash demo.sh [options]`
does the same from an unzipped release. The demo runs git and the threads without `GIT_DIR`,
`GIT_WORK_TREE`, `GIT_INDEX_FILE` and the other variables that aim git at one repository, so
running it from a git hook or `rebase --exec` never touches that repo. Exit: 0, 1 when git is
missing, the port is taken, the folder is not empty or cannot be made, or a step failed (one
line, the step's own message, no traceback), 2 for usage, 130 when interrupted while building.
`Ctrl-C` while serving, at any point after the build, stops the bridge and exits 0.

## Phone access: `pair` and `devices`

Both talk to the **running** map over loopback, the way its page does (they take the page's UI
session from `GET /`, then call with the map's own origin), so the map must be up. `--port N` (or
`$BRIDGE_PORT`, default 7428) names its port, `--json` prints the answer as one JSON object. The
agent token is never used and never accepted for these calls.

### `pair`

Makes a pairing code (8 characters from `ABCDEFGHJKMNPQRSTUVWXYZ23456789`, single use, ten minutes) and
prints where to type it. `--hours N` (1 to 168) makes a code that waits that long instead; its hash is
kept in `remote-codes.json` beside the paired devices (mode 600), so a restart of the map does not void it:

```
open  https://phone.example.com/pair  on the phone
code  K9DB VX2Y   (one use, expires in 10 minutes)
```

JSON: `{"code", "expires_in", "url"}`. Fails with the bridge's own words when the config has no
`remote` block (`phone access is off: …`), when it was refused (`phone access is refused: <why>`)
or when the listener could not open its port (it keeps trying every 30 seconds; the message says so). Setup and rules: README, "Use it from your phone".

### `devices [--revoke ID]`

Lists the phones that are paired: id, name, when each was paired and last seen. JSON: `{"enabled",
"listening", "public_origin", "error", "pending", "devices": [{"id", "name", "created",
"last_seen"}]}`. `--revoke ID` ends that device's session at once (its next request is a 401 and
the page sends it back to the pairing page) and prints `revoked ID`; an unknown id is exit 1
(`no such device`).

## Threads, headless: `thread`

The thread engine without a browser. A thread is the same thing the page's **N** starts: an
agent works on your words in a folder (its own git worktree on branch `cm/<id>` when the folder
is in a repo), a small supervisor process writes every exit code and verify result as a receipt,
and you review the diff and merge it or set it aside. The CLI calls `tasks.py` directly.

### Which store

Every `thread` subcommand takes:

- `--config FILE` and `--root DIR`: the same config `serve` uses. With `--root DIR`
  the config is `DIR/.container-map/container.json` and the threads live in
  `DIR/.container-map/tasks/` (or the config's `tasks_dir`).
  Given neither, the map is found in this order: `$CONTAINER_CONFIG`; `$CONTAINER_ROOT`; the
  nearest folder above the one the thread works on (`thread new`: its TARGET, a file counts as its
  folder; then the current folder) or, for every other subcommand, above the current folder, that
  holds `.container-map/container.json` (or `.container-map/tasks` or `run`, which a map served
  without `init` has), as if you had passed `--root` with the folder that holds it; last, the
  tool's own `container.json` (else the defaults). So on a workspace you ran `init` on, `thread`
  works from anywhere inside it with no flags. A TARGET outside the map it found is refused with
  the roots and the config file that were checked.
- `--json`: one JSON object on stdout (keys listed per subcommand below). With `--wait` the
  streamed log goes to stderr, so stdout stays one object.

Pass the same `--root` / `--config` as the running map and the two share one task store: a
thread started here shows on the page, and the other way round. It also works with no map
running. The store locks each thread on its own, so the CLI and the bridge can act on threads
at the same time. Two more file locks keep them from racing each other: a store-wide
`.store.lock` in the tasks folder is held from the check for a free place until the run is on
record (so the CLI and a map cannot both take the last of `max_running`), and
`container-map.lock` in the repo's git common dir is held across a whole merge, set-aside or
worktree creation (so two of them never change one repo at once). A process that waits more than
10 minutes for either gives up with a 503 naming it; a process that dies lets go of its locks,
except the repository's while a `git` run it began is still going: that run holds the lock until it
ends, so a killed CLI or map cannot be followed by a second merge on top of the first.
They are `flock`s: Linux and macOS. Windows has no `fcntl` and is not supported natively; on a
Windows machine run everything inside WSL2 (a Linux distribution, `wsl --install`), where the
Linux steps apply as written. `cli.py` says so and exits 1 on Windows itself (`doctor` reports it).

The engine's own `git` calls drop the caller's `GIT_*` variables (`GIT_DIR`, `GIT_WORK_TREE`,
`GIT_INDEX_FILE`, …), so a map or CLI started from a git hook or inside a repo command still
works on the repo it names; `GIT_SSH*`, `GIT_ASKPASS` and `GIT_TERMINAL_PROMPT` are kept.

The agent, the check, the verify line and the setup line get your environment without the map's
own variables, by name, and without the git variables that aim a command at one repository:
`BRIDGE_PORT`, `BRIDGE_HOST`, `BRIDGE_LIVE_PATH` (the file with the agent token), `BRIDGE_LOG_PATH`,
`BRIDGE_GAME_PATH`, `BRIDGE_RENDER_PATH`, `BRIDGE_SEATS_PATH`, `BRIDGE_SYNCED_DIR`,
`CONTAINER_CONFIG`, `CONTAINER_ROOT`, `CONTAINER_OUT`, `CONTAINER_RENDER_SECONDS`,
`CONTAINER_TMUX_SESSION`, `CONTAINER_WORK_ROOT`, `CONTAINER_AREAS_JSON`, and `GIT_DIR`,
`GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`,
`GIT_COMMON_DIR`, `GIT_NAMESPACE`, `GIT_PREFIX`. Everything else is kept: `PATH`, `HOME`, `LANG`,
`TERM`, the agent's login variables, your git identity, config and TLS variables (`GIT_AUTHOR_*`,
`GIT_COMMITTER_*`, `GIT_CONFIG_*`, `GIT_SSL_*`, `GIT_HTTP_*`, `GIT_SSH*`, `GIT_ASKPASS`), another
tool's `CONTAINER_*` (a remote podman's `CONTAINER_HOST`), and the `env` of the model you chose.
The wingmate's model command and the board's hand-offs (a configured runner, your dispatch tool)
start in the same environment.

A thread whose creation was cut short (the CLI killed after the worktree was made, before the
thread's record was written) is finished the next time any store opens, and on every tick: it
comes back as a `failed` thread whose note says the creation was interrupted, with its worktree
and branch kept, so you can `reply` to start the agent, `merge` or `aside` it. If nothing had
been made yet it is closed as `done`. A creation still at work is left alone.

On macOS (no `/proc`) the engine reads process groups from `ps` and each process's folder from
`lsof`, both of which ship with macOS, so `stop` on a lost thread and the leftover check work
there too. Without `lsof` a group's folder is unknown and the engine will not take it for the
thread's, so it refuses to stop what it cannot show is its own.

Agents come from the config's `runners`; with none configured, the agent CLIs on this shell's
PATH (`claude`, `codex`, `gemini`, `aider`) are offered. `thread list --json` lists them under
`agents`.

### The oracle loop and `--wait`

`--wait` follows the run until it settles: it streams the run's log (tool calls, commands, the
agent's words; a retry's or a reply's run as it begins), and every 3 s it runs the oracle loop
(`TaskStore.tick`) itself. A thread whose verify failed with retries left goes back to its agent
with the failure, and the wait follows the new run. No bridge is needed for this. With the map
running, both may tick; each retry still starts once.

The tick covers the whole store, the same as the bridge's: while you wait, other threads with a
retry due also go out. Without a running map and without a `--wait`, a due retry waits:
`thread show` says `retry due`.

A retry that can never go out (the thread's agent is not offered any more, or its worktree is
gone) is put on record as blocked: `thread show` says `retry blocked: <reason>`, the summary's
`blocked` holds the reason and `retry_due` turns false, and a `--wait` on it ends with exit 1
instead of polling for ever. `reply` starts a new run when the cause is fixed.

A verify or setup line that is still running after its cap (600 seconds each) is ended and its
receipt says so: `timed_out: true` and `limit: 600` beside the exit code (-15), the run log gets
`timed out after 600 s`, `thread show` prints `timed out after 600 s` and the merge blocker reads
`verify timed out after 600 s` (`setup timed out after 600 s`). The agent's retry prompt says the line
timed out, not that it failed.

After a run that was lost (its supervisor died, so there is no exit code), a reply or a retry tells
the agent so ("Your previous run was lost ...") instead of an exit code of `None`.

A due retry is blocked only by the config file the thread was made with (the thread records its
path in `task.json` as `config`): a store built from another config, such as the CLI next to a
running map, that does not offer the thread's agent leaves the retry due for a store that does. A
thread made with no config file (the agents are found on PATH, each process with its own set: a map
started before an agent was installed has a stale one) has no owner to name, so no store calls its
agent gone and the retry stays due until a store that offers the agent ticks; a worktree that is
gone still blocks it. A thread from before the record is judged as it was.

The setup and verify lines each run in a process group of their own, and whatever they left
running in it (a background helper) is ended before the line's receipt is written, whatever the
exit code; the run's log says `ended N leftover process`. Every group a run recorded is swept
once more before its exit receipt, so nothing a run started keeps changing files during review
or merge.

Ctrl-C stops the waiting, not the thread (exit 130). `thread show ID` picks it up again.

### Exit codes

| code | meaning |
|---|---|
| 0 | done: the thread reached review, the check said PASS, the action went through |
| 1 | the thread ended failed (a blocked verify retry included), stopped or lost; the check was not PASS; or the store refused (no such thread, still running, already closed, at capacity, a store or repo lock held too long, git refused) |
| 2 | usage: unknown option, missing argument, not a number. Also `merge` refused because the evidence argues against it |
| 130 | interrupted while waiting; the thread keeps running |

Errors go to stderr as `container-map thread <sub>: <message>`, the store's own words. With
`--json` an error is `{"ok": false, "error": "<message>", "exit": <code>}` on stdout.

### `thread new TARGET "text" --agent NAME [options]`

Start a thread on TARGET (a folder inside the map's roots; a file means its folder). Prints the
id.

| flag | meaning |
|---|---|
| `--agent NAME` | required: a runner that does work (not a `check`-only one) |
| `--model M` | one of the models that runner offers; default its first |
| `--verify "line"` | one shell line (up to 400 characters) that must exit 0 in the thread's folder for the work to count; it runs after every exit of the agent's own (not after a stop or the time cap) |
| `--retries N` | 0 to 3: how many times a failed verify sends the agent back. Needs `--verify` |
| `--setup "line"` | one shell line run in a fresh worktree before the agent starts, again on a follow-up until one setup has passed; defaults to the config's `worktree_setup` for that repo. Skipped for an in-place thread |
| `--in-place` | work in the folder itself, not in a worktree (in a repo, your uncommitted files are copied aside to the thread's `before/` first) |
| `--wait` | follow the run and the retries; exit 0 at review, 1 when it ends failed (or its retry is blocked), stopped or lost |

Plain output: the id on the first line. With `--wait`, then the log, then the result: status,
the agent's exit code, the attempt count, the verify receipt (with the tail of its output when
it failed), the evidence line (`evidence none|verified|checked|verified and checked`), any merge
blockers and what to run next. A passing verify is the thread's own test, not proof of
correctness: run `thread check` before you merge anything that matters.

JSON: `{"ok", "id", "thread", "waited", "verify_tail"}`. `thread` is the thread summary (the
same object as `GET /agent/tasks` in [AGENT-API.md](AGENT-API.md): `status`, `rc`, `runs`,
`attempts`, `verify`, `check`, `setup`, `retry_due`, `blocked`, `merge_blockers`, `evidence`, `branch`, `cwd`, …).
`verify_tail` is the end of the last verify output (`null` without `--wait`).

### `thread list [--open]`

Every open thread and the newest 200 closed ones (merged, set aside, done), newest first, one
line each: id, status, agent, title. The limit of 200 is for closed history only: an old thread
that is still open is never cut off the list, and a long-running or lost one still counts toward
`max_running`. `--open` leaves out the closed ones.

JSON: `{"ok", "threads", "agents", "max_running"}`.

### `thread show ID [--run N]`

The summary (status, agent, folder and branch, verify receipt, check verdict, merge blockers)
and one run's whole log, parsed the way the page shows it. `--run N` picks a run; default the
newest (a check is a run too).

JSON: `{"ok", "id", "thread", "run", "lines", "prompts", "verify_tail", "setup_tail",
"closed_note"}`. `lines` are `{"k": kind, "s": text}`; `prompts` lists every run's prompt with
its kind, exit code and whether it was an automatic retry.

### `thread diff ID`

The patch: everything the thread committed or left uncommitted, against the commit it started
from. Plain output is the raw patch (pipe it to `git apply --check`). Outside git there is no
line diff: the changed files are listed and a note goes to stderr. After a merge or set-aside the
note says where the work went.

JSON: `{"ok", "id", "files", "patch", "truncated", "note", "where"}`.

### `thread reply ID "text" [--wait]`

A follow-up in the same folder or worktree; Claude Code and Codex continue the maker's own
session. Refused while something runs on the thread or after it is closed. `--wait` as for `new`.

JSON: as `new`.

### `thread check ID --agent NAME [--model M] [--wait]`

A second opinion: the checker reads the task and the diff with its read-only command, never
edits, and ends with `VERDICT: PASS` or `VERDICT: FAIL`. It is recorded as a run of its own; the
thread's status stays what the work made it. The verdict is the last line of the checker's final
answer (its `result` or last message; a tool's output never counts) that starts with `VERDICT:`,
after at most markdown (`## `, `- `, `* `, `> `, `_`, `**`); `VERDICT: FAIL, it hardcodes VERDICT: PASS`
reads FAIL, and a log with no such line reads `no verdict`.
A recheck that crashes, is stopped or is lost does not replace an earlier PASS or FAIL for the same
work: that verdict stays in force (`last_verdict`) until new work makes it stale or a newer check
finishes with its own. The checker must offer a read-only command (a
runner with `check_cmd`, or `"role": "check"`). Without `--wait` it prints the run number; with
it, the check's log and then the verdict and its reason line. Exit 0 only on PASS.

JSON: `{"ok", "id", "thread", "run", "waited", "verdict", "reason"}`.

### `thread stop ID`

Ends the running agent (or check): the supervisor stops the agent's whole process group and
writes the receipt. The status reads `stopping` until it has, then `stopped`. No verify runs
after a stop. A stop never waits for ever on a process that will not die: after SIGTERM (15 s)
and SIGKILL (5 s) the supervisor waits 5 s more, gives up on it and writes the receipt. On a
lost thread (its supervisor is gone) `stop` ends the process groups the run recorded that still
work in the thread's folder, then writes the receipts itself.

JSON: `{"ok", "id", "thread"}`.

### `thread merge ID [--force]`

Merges branch `cm/<id>` into the branch the thread started from (whatever the agent left
uncommitted is committed onto it first, except untracked build junk the config's `merge_skip`
names: `__pycache__/`, `node_modules/` and the like stay off the branch, and the receipt and the
message list them). That commit and the merge itself run with the repository's git hooks off
(`core.hooksPath` pointed at nothing and `--no-verify`): a failing pre-commit hook cannot make a
thread unmergeable, even with `--force`, and a hook planted in the shared `.git` does not run when
you merge; the `merged` receipt carries `"hooks": "off"` and the message says no hook was run. Creating the
thread's worktree is the one git call that keeps the repository's own hooks, on purpose: `git worktree add` runs the
checkout hooks (`post-checkout`, git-lfs) a repository relies on to build a working folder, so a hook already in the
shared `.git` runs then. The map
runs no git in a worktree whose `.git` file the agent changed (it must still name the folder git keeps for
that worktree inside the repository's own `.git/worktrees`): the card says so, a merge is refused, and a
discard sets the whole folder aside unread. Git
refuses to overwrite your uncommitted work and
nothing changes. When the evidence argues against it (`merge_blockers`: a failed verify, one that
`timed out after N s`, a verify that could
not run or never ran on a stopped or lost thread, a stopped or lost last run whatever the verify
(none included), a FAIL verdict newer than the work, a failed run (one the time cap ended reads
`the agent's run was killed at its time cap (30 min)`),
`the work changed after verify`, `the work changed after check`, evidence that cannot be set against
the work (below), and with `merge_requires_check` on,
`no independent check` until a check run has a fresh PASS), the merge is refused with exit 2 and the blockers are
listed; `--force` merges anyway and the merge receipt records it as forced. An agent that changed
nothing closes the thread as done.

`the work changed after verify` and `the work changed after check`: the verify receipt and a check's exit
receipt carry a digest of the worktree's content (what a merge would land: the HEAD tree plus
uncommitted, new and deleted files, so committing the same content changes nothing). Files git ignores
and untracked files the config's `merge_skip` names (`__pycache__/`, `node_modules/` and the rest of the
list: the merge leaves them off the branch) are not part of it, so a verify or check that runs the tests
and writes `.pyc` files into a repo with no `.gitignore` does not make the merge wait. Each digest is
compared with the worktree as it is now, by itself: a worktree whose content differs from the digest of
its verify was edited after the verify, one that differs from the digest of the check whose verdict is
in force was edited after the check, and both are listed when both hold. The two digests need not match
each other. Threads from before the digests, and in-place threads, have none and are not compared. A
digest git could not take when the receipt was written, and a worktree that cannot be read when you merge
(its folder is gone, or git fails there), are blockers of their own (`the work could not be fingerprinted
at verify, ...`, `the work cannot be compared with its evidence: ...`): nothing then shows that the work is
what was verified, so the merge waits for `--force`. A thread whose worktree you removed by hand, with no
verify or check to compare, still closes as before. A
checker that leaves new files that are not junk behind (for example by rerunning the verify) changes
the content it vouches for; the digest is taken when it ends.

Two refusals no `--force` overrides, both before anything is written: the repo has a merge, rebase,
cherry-pick, revert or am of your own waiting to be finished ("your repo has an unfinished …"; the
tool never aborts what it did not start), and the worktree is not on `cm/<id>` (the agent switched
branch or detached HEAD, so its commits there would be lost; the message names the `git -C … switch`
that puts it right).

JSON: `{"ok", "id", "thread", "merged", "forced", "merge_blockers", "message"}`.

### `thread aside ID`

Sets the thread aside: what the agent left uncommitted is committed onto `cm/<id>`, the worktree
is removed, the branch stays (untracked build junk named by `merge_skip` is left out of that
commit, as in `merge`). Refused while the worktree is not on `cm/<id>` (see `merge`). Nothing is
deleted: ignored files that are not build byproducts keep the worktree. Only built-in junk
(`__pycache__/`, `node_modules/`, `.mimocode/` and the rest of the default `merge_skip`) and ignored
build folders go with the worktree; any other file that would (one only your own `merge_skip` names,
say) keeps it, and so does a worktree git cannot remove (a read-only build folder): it moves to
`<worktrees_dir>/<id>.retired-<stamp>` (`<task>/worktree.retired-<stamp>` for a thread made before
worktrees left the receipts) and the message names what was kept or why. Never gated by the evidence.

JSON: `{"ok", "id", "thread", "message"}`.

### `thread done ID`

Closes a thread that worked in place (or one whose worktree is already gone). A worktree thread
is closed by `merge` or `aside`.

JSON: `{"ok", "id", "thread", "message"}`.

## Workflows you can watch: `workflow` (alias `wf`), `show`, `area set`

A workflow is a few steps, each where it works (a folder on the map, `machine:<name>`, a
`place:<name>` with no folder, or this machine), that any agent puts on the map and reports as it
goes; the map draws each step in its place with its state, what it says and what it made. Task steps
run through the thread engine (as `thread new` does), gates wait for you. `wf` works on the same store
as the running map (`workflows_dir`, beside the threads) and needs no bridge; it finds the map's config
the way `thread` does (`--config FILE`, `--root DIR`, `$CONTAINER_CONFIG`, or the map above the current
folder). The HTTP side of the same calls: [AGENT-API.md](AGENT-API.md#workflows-you-can-watch-and-shows).

| command | what it does |
|---|---|
| `wf new SPEC.json\|- [--plan] [--wait] [--json] [--token-file PATH]` | make one from a spec (`wf schema` shows it); prints the id and the token, once, or writes `{"id","token"}` to PATH (mode 600, made new: an existing file is refused and left alone) |
| `wf new "Title" --step "Label@/path" [--step "Label@place:docs site" ...] [--by NAME]` | the quick form: note steps, each after the one before |
| `wf step [ID] KEY --state working\|done\|failed\|skipped [--says T] [--progress 120/800] [--output PATH\|TEXT]` | report a note step (a task step's own agent may send `--says`, `--progress`, `--output`) |
| `wf pulse [ID] [KEY]` | still at it (keeps the workflow from reading "no news"); always exits 0, silently, so a hook can run it after every tool call |
| `wf done [ID]` · `wf stop [ID] [--why T]` | the agent ends it; stops it (no new launches) |
| `wf list` · `wf show [ID] [--wait] [--json]` | the workflows; one as a text stepper (`--wait`: until it is done, failed or stopped, ticking the engine itself every 3 s) |
| `wf start ID` · `wf gate ID KEY --pass\|--fail [--why T]` | yours: start a planned one (it lists what runs as you), approve or reject a gate |
| `wf new --from ID` | yours: Run again, a planned copy of a finished workflow |
| `wf templates` · `wf new --template NAME --at PATH --goal "one sentence" [--runner R]` · `wf save ID --as NAME` | the three built-in templates (fix-and-check, review-folder, two-opinions) and your saved ones; start one (yours, planned); keep a finished workflow as a template (yours) |
| `wf wrap [--title T] [--at PATH] -- CMD...` | run CMD as a one-step workflow: working, then done or failed (CMD runs even when the map refuses the workflow); exits with CMD's code, or 128 plus the signal's number when a signal ended it. On a terminal CMD runs in a process group of its own, in your terminal's session and as its foreground job: Ctrl-C and Ctrl-Z reach it as they would in a shell, and a command that asks through `/dev/tty` (a `sudo`, `ssh` or git password prompt) can. With no terminal on standard input it runs in a session of its own (no `/dev/tty`). INT, TERM or HUP sent to the wrapper itself stops CMD and everything it started: INT (or the signal received), then TERM after 10 s, then KILL after 3 s more, each further signal skipping to the next step; the step is marked failed ("interrupted") and the wrapper exits 130, or 128 plus the signal |
| `wf demo` | a 4-step workflow on this map, part way through (for a look, probes and screenshots) |
| `wf schema [--json]` | the spec, the show ops, the control actions and every bound |
| `wf retire --older 30d [--attic DIR]` | move closed workflows older than that to `ROOT/_attic/workflows-<date>` (MANIFEST.tsv, UNDO.sh); nothing is deleted |

The id and the token come from `$CM_WORKFLOW` and `$CM_WORKFLOW_TOKEN` (a task step's agent has
them; its token is good for its own step's words only), or from `--token-file PATH` (mode 600, else
refused): a session whose shell forgets its variables between calls keeps them there. "Yours" means a
terminal: those commands are refused (exit 1) when standard input is not a terminal, a speed bump so an
agent's shell does not press your buttons. Exit: 0 ok; 1 refused (the reason on stderr), or a workflow
`--wait` saw fail or stop; 2 usage; 130 interrupted: a `--wait` stops waiting and the workflow goes on;
a `wf wrap` stops its command and marks the step failed.

`container-map show` paints on the running map for a while (the agent token from its live file, found
as `gamectl.py` finds it: `--live-file`, `$BRIDGE_LIVE_PATH`, else `--root`/`--port`):

| command | what it does |
|---|---|
| `show PATH\|UID\|place:NAME "caption" [--seconds N]` | point at it: an arrow there, the caption in the status bar, 5-120 s |
| `show --focus PATH\|UID` · `show --follow ID` | centre a planet; select a workflow and keep it in view (as your setting "Agents can move my view" allows) |
| `show card --at X --title T [--row K=V ...] [--text T] [--bar 3/9]` | a small card at X |
| `show chart --at X --label T --values 1,4,9 [--unit ms] [--kind spark\|bars]` | a sparkline or bars at X |
| `show page --title T --html-file FILE\|- [--at X]` | your own HTML page: a card on the map that the person opens in a sandboxed panel (below) |
| `show put SPEC.json\|- [--ttl 300]` · `show clear ID` | any show (`{ops, at, by, ttl, id}` or a list of ops); take one down |

`--by NAME` (default `$CM_AGENT_NAME`, else "an agent": the map names the author on it) is taken by every
command above but `clear`. `card`, `chart`, `page` and `put` make a stored show, and take `--ttl SECONDS` (30-900, default 300;
a show with a page: up to 86400, default 1800)
and `--id ID` (the same id replaces it); `card` and `chart` also take `--xray` (the 3D view draws it through
planets; in a `put` spec set `"xray": true` on an op). The point, `--focus` and `--follow` are control actions the
map carries out, not shows it stores: they take `--by` (and `--seconds N` for the point) and ignore `--ttl`,
`--id` and `--xray`; `clear` takes only the flags that find the map (`--live-file`, `--root`, `--port`). Shows live in the map's memory and never
start or change work. Exit: 0 shown, 1 refused or no map answers, 2 usage.

`show page` reads the page from FILE (`-` reads standard input), which must be UTF-8 text of at most
256 KiB with no control characters but newline, return and tab. The person opens it from its card on
the map (a toast says it arrived; nothing opens by itself) in a panel inside the map: an iframe with
`sandbox="allow-scripts"`, whose document comes from the bridge with a content security policy and a
`Connection-Allowlist` that let nothing in or out. Scripts run, so a chart or a dashboard works, but everything
it needs is in the file: `<script>` and `<style>` blocks (and `on...` attributes), images and fonts as `data:`
or `blob:` URIs. It cannot reach the map, the person's session or the page around it, and in Chrome 152+ not
one of 31 ways of reaching a listening port arrives (measured; [AGENT-API.md](AGENT-API.md) says what other
browsers add). Up to 4 pages are live at once, a new one retires the oldest page, and each lives 30 minutes unless `--ttl` says otherwise (up to 24
hours). With no `--at` it sits at `place:pages`. A phone shows the card and says to open it on the
desktop. A page that
the bridge skips (a bad title, over the size) is exit 1 with the reason on stderr. An agent that speaks MCP
does the same without the shell: [MCP.md](MCP.md).

`container-map area set PATH|area:NAME --kind K [--glyph G]` (yours) says what a planet holds, so the
map draws it as that: `--kind` is one of `code`, `knowledge` (notes), `reference`, `data`, `media`,
`config`, `shared`, `archive`, `unknown` (not sorted); `--glyph` one of `code`, `notes`, `money`,
`health`, `career`, `journal`, `learning`, `archive`, `sync`, `tools`, `home`, `star`. It goes into the
`areas.json` the map reads (the bytes it replaces are kept as `areas.json.prev`; the `.prev` that was there moves
to the first free `.prev.1`, `.prev.2`, ..., so no backup is written over or deleted; a file or folder that is a link is left
alone); the planet card's Change does the same from the page.

### An agent session's hook

An agent harness that runs a command after every tool call can keep its workflow alive. For Claude
Code, paste this into the `"hooks"` of its `settings.json` (yours to edit; the session writes `.cm-wf`
with `wf new --token-file .cm-wf`):

```json
{"hooks": {"PostToolUse": [{"matcher": "*", "hooks": [{"type": "command",
  "command": "container-map wf pulse --token-file \"$CLAUDE_PROJECT_DIR/.cm-wf\""}]}]}}
```

The whole session, step by step, is in [AGENT-API.md](AGENT-API.md#example-a-claude-code-session).
