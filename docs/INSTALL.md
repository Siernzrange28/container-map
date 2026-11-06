# Install container-map

Linux or macOS. Needs **Python 3.10 or newer**, **git** and **bash**. Windows is not supported
natively: on a Windows machine install WSL2 (`wsl --install`) and do everything below inside it.
No packages, no build step, no network. Optional: tmux (seats and doors), an agent CLI (tasks),
systemd on Linux (the service). macOS has no `/proc`; the thread engine uses `ps` and `lsof`
there, both of which ship with it.

## 1. Get it

```
unzip container-map-1.0.0.zip -d ~/apps
cd ~/apps/container-map-1.0.0
```

No `unzip` on the machine (a minimal Ubuntu, a container)? Python can unpack it:

```
python3 -m zipfile -e container-map-1.0.0.zip ~/apps
cd ~/apps/container-map-1.0.0
```

(That form does not restore file permissions. Nothing needs them: the shell scripts are always
run as `bash script.sh`.) Check the install with `python3 cli.py --version`, which prints
`container-map 1.0.0`.

Everything runs from this folder with `python3 cli.py …`. Keep it where it is: the service file
written in step 4 points at it (moved it? run `install` again).

pip works too, and gives a `container-map` command that takes the same arguments, but the
unzipped folder is the tested path. Do not use `pip install --user`: on a Python the OS manages
(Ubuntu 23.04 and later, Debian 12 and later, Homebrew's python) pip refuses with
`externally-managed-environment` (PEP 668). Use a virtualenv, or pipx, which makes one for you:

```
python3 -m venv ~/.venvs/container-map
~/.venvs/container-map/bin/pip install ~/apps/container-map-1.0.0
~/.venvs/container-map/bin/container-map doctor ~/work
```

or `pipx install ~/apps/container-map-1.0.0` and then `container-map doctor ~/work`. (On Debian and
Ubuntu `python3 -m venv` may stop with "ensurepip is not available": `sudo apt install python3-venv`
first.) The bridge keeps its log and a few small state files next to its own code, so the install has to
be somewhere you can write; a venv or pipx always is. Its credentials file is not one of them: it
lives in your root's `.container-map/run/`. The CLI hands the Python it runs on to the scripts it
starts, so a venv's Python is the one the bridge and the renderer use. Python 3.10 or newer: pip
refuses an older one with a message that says so.

Want to see the thread loop before pointing it at your files? `python3 cli.py demo` builds a
throwaway workspace and runs it with a scripted agent: no agent CLI, no login (see
[CLI.md](CLI.md)).

## 2. Check the machine: `doctor`

```
python3 cli.py doctor ~/work
```

Lists the version, python, git, bash, tmux, systemd-run, the service manager, each agent CLI it finds on PATH
(claude, codex, gemini, grok, aider) with its path (with none it says so and points at
`container-map demo`), the config file in use, the roots, where state goes, whether the port
(7428) is free and the bridge's live file for this root and port. Exit 0 = ready. Exit 1 prints
`not ready: …` with every reason: a missing piece, a root that does not exist (it names the folder
it looked for; nothing falls back to `~/work` and nothing is created) or a `container.json` that
does not parse (its path, the error and its line). A busy port is reported either way: a bridge of
this map on it is named as such; anything else adds a `note:` that `serve` and `install` will
refuse it (pass `--port N`).

## 3. Write a config: `init`

```
python3 cli.py init ~/work
```

Writes `~/work/.container-map/container.json` (mode 600) and prints what it found: the name, how
many planets of which kind, the agents, the wingmate. What is in the file:

- `name` (the folder's name) and `work_root` (the folder);
- `planets`: every non-hidden top-level folder, `code` when it holds `.git`, `pyproject.toml` or
  `package.json`, else `knowledge` (edit the list to choose your planets);
- `runners`: the agent CLIs found on PATH right now (claude, codex, gemini, aider), each as a full
  command with the models it offers; Claude Code offers sonnet (the default), haiku and opus. With
  none on PATH, `runners` is `{}` and threads have no agent until you install one (see "Agents on
  PATH" below);
- `wingmate`: `claude -p` with the models haiku, sonnet and opus (and Codex (OpenAI) through `codex exec`
  when codex is on PATH too), haiku the default, when claude is on PATH; else none (it then only
  explains the map);
- `tmux_session` (the session you ran it from, else empty) and the example's everyday settings,
  with its sample paths blanked: `theme`, `processes`, `decision_scan`, `decision_words`,
  `detect_runners`, `agent_verify` (`false`: only the page can attach a verify line), `task_scope`,
  `no_ai_trailers`, `tick_seconds`.

`planets` is the list of folders that were there. Leave it empty (`[]`) and the map shows every
top-level folder of the root, new ones included. With no agent CLI on PATH, `runners` and `wingmate`
stay empty and `detect_runners` stays on: a CLI you install later shows up without editing the file.
A folder that is not there gets an error; `init ROOT --create` makes it. In a git repo, `init` adds
`.container-map/` to the repo's `.git/info/exclude`.

It never overwrites: if `container.json` exists it writes `container.json.new` beside it and says
so. Every other key is optional and documented in [CONFIG.md](CONFIG.md), `container.example.json`
and `container.sources-example.json` next to `cli.py`. No config at all also works (zero-config).

## 4. Run it: `install` (or `serve`)

```
python3 cli.py install ~/work --dry-run    # print the service file, write nothing
python3 cli.py install ~/work              # write it, enable it, start it
```

Then open <http://127.0.0.1:7428/>. `--port N` picks another port.

`install` checks the port first and stops with exit 1, before writing anything, when something
other than this map's own bridge holds it (a map you installed earlier for another folder: run
`uninstall` first). After it starts the service it waits up to 15 s for the new bridge: its live
file and an HTTP 200 on the port. Only then does it print "installed and started". If the bridge
does not come up it says why, prints the service's last log lines (the journal, or launchd's log
file) and exits 1; the unit stays, so `uninstall` is the way back.

- **Linux**: a user unit `~/.config/systemd/user/container-map.service` running
  `python3 cli.py serve ~/work --port 7428`, enabled and started with `systemctl --user`. Logs:
  `journalctl --user -u container-map -f`. To keep it running while you are logged out:
  `loginctl enable-linger $USER`. On WSL, systemd must be on (`[boot] systemd=true` in
  `/etc/wsl.conf`), else use `serve`.
- **macOS**: a launchd agent `~/Library/LaunchAgents/local.container-map.plist`, loaded with
  `launchctl bootstrap gui/$(id -u)`. Log: `~/Library/Logs/container-map.log`.
- Elsewhere `install` says it cannot, and you run `serve` under your own supervisor.

The service gets the PATH of the shell you ran `install` from, so the agents `doctor` found are
found by the service too. Installed a new agent CLI later? Run `install` again.

In the foreground instead, Ctrl-C stops it:

```
python3 cli.py serve ~/work
```

`serve` renders the page once before it prints the URL, and exits with one line if the port is
taken. Ctrl-C ends it with one line (`container-map: stopped`), exit 0, and takes the bridge and
the renderer with it.

## Agents on PATH

Tasks need at least one agent CLI on PATH, installed and logged in the usual way (run it once by
hand first). `claude` (Claude Code), `codex`, `gemini` and `aider` are offered automatically;
anything else (grok, a wrapper script) goes under `"runners"` in container.json as an argv (see
`container.example.json`). The map stores no credentials and makes no network calls of its own;
an agent you start talks to its own provider.

`init` bakes the agents it finds into `runners`, and `install` gives the service the PATH of the
shell you ran it from. Installed an agent after that? Run `install` again (or restart `serve`). If
`init` found none, `runners` is empty and `detect_runners` is on, so the next start of the map
offers whatever is on PATH then.

## The first thread in 10 minutes

1. `doctor`, `init`, `install` (or `serve`) as above; open <http://127.0.0.1:7428/> (or the port
   you gave). The first screen is the map of the planets `init` wrote.
2. Press **N** (or "+ New task" in the right rail). The composer asks for:
   - the words: what you want ("add a --version flag and a test for it");
   - **Folder**: click a planet on the map, or type a path inside one of your planets;
   - **Agent** and **Model**: the agents `init` found, and what each offers (empty list: no agent
     on PATH, see above);
   - **Safe copy** (ticked): in a git repo the task gets its own checkout (a git worktree);
   - optional **Setup** (one shell line run once in the new safe copy, e.g. `npm ci`) and **Test that must pass**
     (one shell line that must exit 0 in that folder, e.g. `python3 -m unittest -q`) with
     **Retries** (0 to 3, 2 by default; they only apply when there is a test line).

   Start it with **Start task** or Ctrl+Enter.
3. In a git repo with at least one commit, inside your planets, the agent works in its own
   worktree on branch `cm/<id>`; your checkout is not touched. Anywhere else it works in place.
   The right panel streams what it does (Conversation tab).
4. When it stops it needs your review: open the **Changes** tab, then **Accept changes** (the
   tactical look says **Merge into <branch>**), **Reject (copy kept)** (**Set aside**), **Ask a second
   opinion** (**Check it**: a second agent reads the work and answers PASS or FAIL) or write a
   follow-up in the reply box. Outside a repo the button is **Mark done**. **K** opens the Agents
   tab of the side panel, which lists every task.

## Where state lives

- `ROOT/.container-map/`: `container.json` (config), `game.html` (the page), `tasks/` (one folder
  per thread: what was asked, its events and run logs), `tasks-worktrees/` (the threads' git
  worktrees, one folder per thread, kept apart from the receipts), `history.jsonl`, `verbs/`,
  `cron-syslog.json`, `render-cache/` (what the renderer remembers between cycles), and, only when phone access is on, `remote-sessions.json` (the paired
  devices, hashed) and `remote-audit.log`.
- `ROOT/.container-map/run/bridge-<port>.json`: the bridge's port and agent token (mode 600, new
  on every start, removed when it stops). One per root and port, so two maps never overwrite each
  other. `python3 gamectl.py` and `doctor` find it; the page's file preview never serves it.
- The tool folder: `bridge.log`, `.task-runs.jsonl`, `.quest-rulings.json`, and, only if it
  was already there, the older `.bridge-live`, which keeps being updated for the hooks that read
  it (not while another running map owns it; a stop leaves it in place, stale, and the next start
  refreshes it). `run/` has a `.gitignore` of its own, so the token never reaches a repo.
- The service file and its log, above.

Nothing else is written.

## Stop and uninstall

- Stop for now: `systemctl --user stop container-map` (Linux) or
  `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/local.container-map.plist` (macOS).
- Uninstall: `python3 cli.py uninstall` (`--dry-run` shows what it would do). It stops and disables
  the service and renames its file to `….retired-<date>-<time>`; nothing is deleted. If the
  service cannot be stopped (no bus, no `systemctl`) it prints why, keeps the file and exits 1, so
  running it again still finds the service. A service that was never loaded counts as stopped. If
  `install` failed before the service was ever loaded, `python3 cli.py uninstall --force` retires
  the unit file anyway.
- Threads already running keep running until they finish (each runs in its own scope when
  systemd-run is there); stop them from the page (**K**) first if you want them gone.
- `ROOT/.container-map/` and the tool folder stay until you remove them yourself.
