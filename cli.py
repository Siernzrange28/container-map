#!/usr/bin/env python3
"""container-map — point it at a folder, get a star system of what is actually there.

  container-map --version                     print the version and exit
  container-map render [ROOT] [--out FILE] [--port N]
                                              write the page once (file://-openable, static);
                                              its link to the live map points at port N
  container-map serve  [ROOT] [--port N] [--lock]
                                              bridge + 15 s renderer; prints the live URL
                                              (--lock: the page opens only through a private link)
  container-map link [ROOT] [--port N]        the private link of a locked map that is running
  container-map demo [--no-serve] [--port N] [--static] [--root DIR]
                                              try the thread loop with a scripted agent, no login
  container-map config [ROOT]                 print the resolved config (what will be read)
  container-map doctor [ROOT] [--port N]      check this machine: python, git, agents, config, port
  container-map init ROOT [--create]          write ROOT/.container-map/container.json from ROOT
  container-map install ROOT [--port N] [--dry-run] [--lock]
                                              run serve as a user service (systemd or launchd)
  container-map uninstall [--dry-run] [--force]
                                              stop that service; its file is renamed, not deleted
  container-map thread new|list|show|diff|reply|check|stop|merge|aside|done ...
                                              threads without a browser (`thread --help`)
  container-map pair [--port N] [--json]      a one-use code to pair a phone (config key "remote")
  container-map devices [--revoke ID] [--port N] [--json]
                                              the paired phones; --revoke ends one at once
  container-map workflow new|step|pulse|done|stop|list|show|start|gate|wrap|demo|schema|retire|templates|save
                                              workflows you can watch on the map (`workflow --help`);
                                              container-map wf is the same
  container-map show PATH|UID "caption" | show card|chart|put|clear ...
                                              an agent paints on the running map for a while
  container-map area set PATH --kind K [--glyph G]
                                              what a planet holds (its body on the map)
  container-map mcp [--root R] [--port N]     the map's agent API as MCP tools on stdin and stdout, for any
                                              agent that speaks MCP (`mcp --help`, docs/MCP.md)

  --config FILE works with every command that reads a config (else ROOT/.container-map/container.json,
  else defaults; `thread` with neither ROOT nor --config looks for it above the folder it works on).
  `demo` builds its own, and `pair`, `devices` and `uninstall` read none: they find the running map
  by its port (`pair`, `devices`: --port N) or the service by its name (`uninstall`).

ZERO-CONFIG: with ROOT and no config file, the map is built from what every machine has —
subdirectories (planets), files (moons), git state, running processes, your crontab and the tmux
session you are in (seats). Receipts layers (digest, dispatch, runners, audit) stay off until a
ROOT/.container-map/container.json names them. Every number is counted from disk at render time.
"""
import contextlib
import errno
import functools
import http.client
import json
import os
import plistlib
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

# The release number and the Python floor live here and nowhere else: setup.py reads both for the
# package metadata, `--version` and `doctor` print the first, the checks below use the second.
VERSION = "1.0.0"
MIN_PYTHON = (3, 10)
# `demo --static` only renders a page, so it needs neither git nor MIN_PYTHON: it runs on the oldest
# Python the renderer's own syntax allows (a dict `|` dict, 3.9)
STATIC_DEMO_PYTHON = (3, 9)

# the folder of the code itself: a cli.py on PATH through a symlink must find render-game.py,
# world.sh and the rest beside the real file, not beside the link
HERE = os.path.dirname(os.path.realpath(__file__))
PY = sys.executable or "python3"
DEFAULT_PORT = 7428
WAIT_SECONDS = 15          # install: how long the new bridge has to show up
STOP_SECONDS = 10          # _stop_child: how long world.sh has to leave after SIGTERM
VALUE_FLAGS = ("--port", "--out", "--config", "--revoke", "--hours",
               # workflow, show and area set (their own parser, _wf_args, reads them; listed so _positional skips their values)
               "--token-file", "--state", "--says", "--progress", "--output", "--step", "--by", "--from",
               "--template", "--at", "--goal", "--runner", "--why", "--title", "--older", "--attic", "--as",
               "--seconds", "--focus", "--follow", "--row", "--text", "--bar", "--label", "--values", "--unit",
               "--kind", "--ttl", "--id", "--live-file", "--glyph", "--html-file")
AGENTS = ("claude", "codex", "gemini", "grok", "aider")
CODE_MARKERS = (".git", "pyproject.toml", "package.json")
UNIT = "container-map.service"
LAUNCHD_LABEL = "local.container-map"


def _child_start(mask):
    """In the child, between fork and exec: SIGINT ignored (an ignored signal stays ignored across
    exec, so none of its Python helpers prints a traceback for the Ctrl-C meant for us), and the
    signal mask the parent had before it held anything back."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def _raise_stop(signum, frame):
    raise KeyboardInterrupt


def _descendants(pid):
    """Every process below `pid` (children, theirs ...), from `ps -axo pid=,ppid=` (the same flags
    on Linux and macOS); [] when ps cannot be run. Read while `pid` is alive: afterwards its
    children belong to init and nothing links them to it."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True,
                             timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return []
    kids = {}
    for line in out.stdout.splitlines() if out.returncode == 0 else ():
        parts = line.split()
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, todo = [], [pid]
    while todo:
        for kid in kids.get(todo.pop(), ()):
            if kid not in found:
                found.append(kid)
                todo.append(kid)
    return found


def _stop_child(proc):
    """Ask world.sh to stop (it stops the bridge and the renderer); when it has not gone in 10 s,
    or at a second Ctrl-C, end what it started too, then it: killing world.sh alone leaves the
    bridge listening on the port (the next serve is refused) and the render loop going. The
    descendants get SIGTERM first (the bridge removes its credentials file on it), and SIGKILL
    when they outlast a few seconds."""
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(STOP_SECONDS)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        below = _descendants(proc.pid) if getattr(proc, "pid", None) else []
        for pid in below:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)
        proc.kill()
        proc.wait()
        end = time.monotonic() + 5
        while below and time.monotonic() < end:
            below = [pid for pid in below if _alive(pid)]
            time.sleep(0.1)
        for pid in below:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)


def _alive(pid):
    """Whether `pid` is a running process (a zombie its parent has not reaped does not count)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            return handle.read().rsplit(b") ", 1)[-1][:1] != b"Z"
    except OSError:
        return True


@contextlib.contextmanager
def _stops_as_interrupts():
    """SIGINT, SIGTERM and SIGHUP raise KeyboardInterrupt while this runs, also when whoever started
    us left SIGINT ignored (a backgrounded job: Python then installs no handler of its own), so a
    stop is never lost. The old handlers come back after. Only the main thread can set them."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    before = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            before[sig] = signal.signal(sig, _raise_stop)
        yield
    finally:
        for sig, handler in before.items():
            signal.signal(sig, handler)


def _call(argv, env=None, cwd=None):
    """subprocess.call that ends cleanly on Ctrl-C, SIGTERM and SIGHUP. The terminal sends Ctrl-C
    to this whole process group; here it becomes a KeyboardInterrupt, the child (which started
    with SIGINT ignored, see _child_start) is asked to stop with SIGTERM (world.sh stops its
    bridge and renderer in a trap) and the interrupt is raised again for the caller to report.
    The three signals are held back while the child starts, so none falls between the fork and
    this process being ready for it, and none is ignored on the child's behalf in this process."""
    if threading.current_thread() is not threading.main_thread():
        return subprocess.call(argv, env=env, cwd=cwd)
    stops = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    held = signal.pthread_sigmask(signal.SIG_BLOCK, stops)
    before, proc = {}, None
    try:
        proc = subprocess.Popen(argv, env=env, cwd=cwd, preexec_fn=lambda: _child_start(held))
        for sig in stops:
            before[sig] = signal.signal(sig, _raise_stop)
        signal.pthread_sigmask(signal.SIG_SETMASK, held)     # one that came meanwhile lands here
        return proc.wait()
    except KeyboardInterrupt:
        if proc is not None:
            _stop_child(proc)
        raise
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, stops)
        for sig, handler in before.items():
            signal.signal(sig, handler)
        signal.pthread_sigmask(signal.SIG_SETMASK, held)


def _die(message, code=2):
    print(message, file=sys.stderr)
    raise SystemExit(code)


def _me():
    """How the user invoked us, for the hints we print."""
    named = os.path.basename(sys.argv[0] or "") == "container-map"
    return "container-map" if named else "python3 cli.py"


def _run(argv):
    """One service-manager command: (exit code, its output). Never raises."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return r.returncode, (r.stdout + r.stderr).strip()


def _positional(args):
    """Arguments that are neither flags nor a flag's value (`serve --port 8000 ~/work` → ~/work)."""
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a in VALUE_FLAGS:
            skip = True
        elif not a.startswith("--"):
            out.append(a)
    return out


def _root(args):
    rest = _positional(args)
    return os.path.abspath(os.path.expanduser(rest[0])) if rest else ""


def _flag(args, name):
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return ""


def _only_flags(args, allowed, cmd):
    """Stop (exit 2) at an option `cmd` does not take, naming the ones it does. A value flag's
    value is not an option; `--` ends the options."""
    skip = False
    for a in args:
        if skip:
            skip = False
        elif a == "--":
            return
        elif a.startswith("-") and a != "-":
            name = a.split("=", 1)[0]
            if name not in allowed:
                _die(f"container-map {cmd}: unknown option {a} (takes {', '.join(allowed)}; "
                     f"`{_me()} {cmd} --help` says more)")
            skip = name in VALUE_FLAGS and "=" not in a


def _port(args):
    raw = _flag(args, "--port") or os.environ.get("BRIDGE_PORT") or str(DEFAULT_PORT)
    if not (raw.isdigit() and 1 <= int(raw) <= 65535):
        _die(f"container-map: port must be a number from 1 to 65535, got {raw!r}")
    return int(raw)


def _config_arg(args):
    raw = _flag(args, "--config")
    if not raw:
        return ""
    path = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isfile(path):
        _die(f"container-map: no such config file: {path}", 1)
    return path


def _not_a_dir(path, note=""):
    why = "it is a file" if os.path.exists(path) else "no such folder"
    return f"container-map: not a directory: {path} ({why}{'; ' + note if note else ''})"


def _child_env(env=None):
    """The environment a child gets: ours, plus the Python this CLI runs on, so world.sh and
    demo.sh use it too and not whatever `python3` the PATH names (this may be a venv)."""
    env = dict(os.environ if env is None else env)
    env["CM_PYTHON"] = PY
    return env


def _prepare(args, check_root=True):
    """(root, child env). config.py reads CONTAINER_ROOT and CONTAINER_CONFIG, so render, serve,
    config and doctor resolve the same file the same way. A root that is not a folder stops the
    command here (doctor passes check_root=False and reports it in its own words)."""
    root = _root(args)
    if check_root and root and not os.path.isdir(root):
        _die(_not_a_dir(root), 1)
    env = _child_env()
    if root:
        env["CONTAINER_ROOT"] = root
    elif check_root and env.get("CONTAINER_ROOT") and not os.path.isdir(env["CONTAINER_ROOT"]):
        _die(_not_a_dir(env["CONTAINER_ROOT"], "it is $CONTAINER_ROOT"), 1)
    config = _config_arg(args)
    if config:
        env["CONTAINER_CONFIG"] = config
    return root, env


def _port_busy(port):
    """'' when 127.0.0.1:port can be bound the way the bridge binds it, else why not."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
    except OSError as exc:
        return exc.strerror or str(exc)
    finally:
        sock.close()
    return ""


def cmd_render(args):
    _only_flags(args, ("--out", "--port", "--config"), "render")
    _, env = _prepare(args)
    out = _flag(args, "--out")
    if out:
        env["CONTAINER_OUT"] = os.path.abspath(os.path.expanduser(out))
    if _flag(args, "--port"):               # the static page links to the live map at this port
        env["BRIDGE_PORT"] = str(_port(args))
    return _call([PY, os.path.join(HERE, "render-game.py")], env=env, cwd=HERE)


def cmd_config(args):
    _only_flags(args, ("--config",), "config")
    _, env = _prepare(args)
    return _call([PY, os.path.join(HERE, "config.py")], env=env, cwd=HERE)


def _page_path(env):
    """Where the renderer will write the page for this environment ('' if the config will not
    load), so the bridge serves the same file."""
    return subprocess.run([PY, "-c", "import config;print(config.load()['out'])"],
                          capture_output=True, text=True, env=env, cwd=HERE).stdout.strip()


def _first_render(env):
    """One render before the URL goes out, so the first page load finds a page, not a 404."""
    rc = _call([PY, os.path.join(HERE, "render-game.py")], env=env, cwd=HERE)
    if rc != 0:
        print(f"container-map: first render failed (exit {rc}); serving anyway, the renderer "
              f"retries every 15 s", file=sys.stderr, flush=True)


def _config_locked(env):
    """Whether the config this environment resolves says "ui_lock": true."""
    r = subprocess.run([PY, "-c", "import config;print(int(config.load().get('ui_lock') is True))"],
                       capture_output=True, text=True, env=env, cwd=HERE)
    return r.stdout.strip() == "1"


def _launch_link(port, key):
    return f"http://127.0.0.1:{port}/?k={key}"


def _is_tty():
    return sys.stdout.isatty()


def cmd_serve(args):
    _only_flags(args, ("--port", "--config", "--lock"), "serve")
    root, env = _prepare(args)
    port = _port(args)
    env["BRIDGE_PORT"] = str(port)
    key = ""
    if "--lock" in args or _config_locked(env):
        # a shared computer: the bridge hands the page out only to whoever brings this key. It goes to the bridge in
        # its environment (the bridge takes it out again) and to the terminal that started serve, nowhere else
        key = secrets.token_urlsafe(32)
        env["BRIDGE_UI_LOCK"] = "1"
        env["BRIDGE_LAUNCH_KEY"] = key
    busy = _port_busy(port)
    if busy:
        _die(f"container-map: port {port} on 127.0.0.1 is taken ({busy}); "
             f"stop what holds it or pass --port N", 1)
    try:
        page = _page_path(env)
        if page:
            env["BRIDGE_GAME_PATH"] = page
        _first_render(env)
        if key and _is_tty():
            print(f"container-map: locked. Open this link (private to you, new at every start):\n"
                  f"  {_launch_link(port, key)}\n  (root: {root or 'config'}; Ctrl-C stops)", flush=True)
        elif key:       # a service's output goes to a log: the key stays out of it
            print(f"container-map: locked, live on 127.0.0.1:{port} (root: {root or 'config'}). "
                  f"The private link is not printed here: run `{_me()} link --port {port}`", flush=True)
        else:
            print(f"container-map: live at http://127.0.0.1:{port}/  (root: {root or 'config'}; "
                  f"Ctrl-C stops)", flush=True)
        return _call(["bash", os.path.join(HERE, "world.sh")], env=env, cwd=HERE)
    except KeyboardInterrupt:        # the normal way to stop a foreground map: no traceback
        print("container-map: stopped", flush=True)
        return 0


def cmd_link(args):
    _only_flags(args, ("--port",), "link")
    if len(_positional(args)) > 1:
        _die("container-map link: takes at most one ROOT (usage: link [ROOT] [--port N])")
    import config
    port = _port(args)
    try:
        live = config.resolve_live(_root(args), port)
    except RuntimeError as exc:
        _die(f"container-map link: {exc}", 1)
    info = config.read_live(live)
    if not info or info.get("port") != port or not config.pid_alive(info.get("pid")):
        _die(f"container-map link: no running map on port {port} (looked for {live}); "
             f"start it with `{_me()} serve --lock`", 1)
    try:
        private = not os.stat(live).st_mode & 0o077
    except OSError:
        private = False
    if not private:
        _die(f"container-map link: {live} is not private (mode 600), so its key is not shown", 1)
    key = info.get("launch_key")
    if not isinstance(key, str) or not key:
        _die(f"container-map link: the map on port {port} is not locked, so it has no private link "
             f"(`{_me()} serve --lock` locks it)", 1)
    print(_launch_link(port, key))
    return 0


# ---- demo -------------------------------------------------------------------------------------

def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _demo_options(args):
    """{flag: True or its value} for demo's own flags; anything else is a usage error."""
    opts, i = {}, 0
    while i < len(args):
        key, eq, value = args[i].partition("=")
        if key in ("--no-serve", "--static") and not eq:
            opts[key] = True
        elif key in ("--port", "--root"):
            if not eq:
                i += 1
                if i >= len(args):
                    _die(f"container-map demo: {key} needs a value")
                value = args[i]
            opts[key] = value
        else:
            _die(f"container-map demo: unknown argument {args[i]} "
                 f"(takes --no-serve, --port N, --static, --root DIR)")
        i += 1
    return opts


def _demo_serve(dest, port, say):
    """Serve the demo workspace the way `serve` does, but keep every file it writes (the bridge's
    runtime file and log, the page) inside the demo folder. Returns the exit code. Ctrl-C at any
    point from the first render on stops what runs and says so."""
    import demo
    side = os.path.join(dest, ".container-map")
    env = demo.clean_env(CONTAINER_ROOT=dest, BRIDGE_PORT=port,
                         BRIDGE_LIVE_PATH=os.path.join(side, ".bridge-live"),
                         BRIDGE_LOG_PATH=os.path.join(side, "bridge.log"),
                         CM_PYTHON=PY)          # world.sh runs the bridge on the Python we run on
    proc = None
    with _stops_as_interrupts():
        try:
            page = _page_path(env)
            if page:
                env["BRIDGE_GAME_PATH"] = page
            _first_render(env)
            proc = subprocess.Popen(["bash", os.path.join(HERE, "world.sh")], env=env, cwd=HERE,
                                    stdin=subprocess.DEVNULL)
            for _ in range(150):                    # up to 15 s for the bridge to listen
                if proc.poll() is not None:
                    say(f"the bridge exited (code {proc.returncode}) before it listened")
                    return proc.returncode or 1
                if _port_busy(port):                # bound by the bridge now
                    break
                time.sleep(0.1)
            else:
                say(f"the bridge did not listen on port {port} within 15 s")
                _stop_child(proc)
                return 1
            say(f"open http://127.0.0.1:{port}/   (Ctrl-C stops the demo)")
            return proc.wait()
        except KeyboardInterrupt:
            _stop_child(proc)
            say(f"stopped. The workspace stays at {dest} (nothing is deleted)")
            return 0


def cmd_demo(args):
    """A fresh demo workspace in a new folder, never beside the code. Live by default: seven folders, three git
    repos, two completed tasks and a third running while the map is served. With
    --static, the older generated workspace as one page for file://."""
    import demo
    opts = _demo_options(args)
    port = 0
    if "--port" in opts:
        raw = opts["--port"]
        if not (raw.isdigit() and 1 <= int(raw) <= 65535):
            _die(f"container-map: port must be a number from 1 to 65535, got {raw!r}")
        port = int(raw)
    live = "--static" not in opts

    def say(text):
        print(f"container-map demo: {text}", flush=True)

    if live and not shutil.which("git"):
        _die("container-map demo: the live demo needs git (threads work in git worktrees); "
             "`demo --static` needs none", 1)
    serving = live and "--no-serve" not in opts
    if serving:
        busy = _port_busy(port) if port else ""
        if busy:
            _die(f"container-map: port {port} on 127.0.0.1 is taken ({busy}); "
                 f"stop what holds it or pass another --port N", 1)
        port = port or _free_port()
    dest = ""
    try:
        dest = demo.new_folder(opts.get("--root"))
        say(f"building the workspace in {dest}")
        if not live:
            config = demo.build_static(dest)
            page = os.path.join(dest, "demo.html")
            rc = _call([PY, os.path.join(HERE, "render-game.py"), "--config", config],
                       env=demo.clean_env(CONTAINER_OUT=page), cwd=HERE)
            if rc != 0:
                return rc
            with open(page, encoding="utf-8") as handle:
                if "DEMO" not in handle.read():
                    _die(f"container-map demo: {page} was rendered without the DEMO banner", 1)
            say("a static page, nothing on it can act. Open it in a browser (file:// is fine):")
            print(f"  {page}")
            return 0
        config = demo.build_live(dest)
        demo.drive(dest, config)
    except (demo.DemoError, OSError, subprocess.TimeoutExpired) as exc:
        _die(f"container-map demo: {exc}", 1)         # a folder that cannot be made, git too slow
    except KeyboardInterrupt:
        say(f"stopped while building. What exists stays at {dest or 'its folder'} "
            f"(nothing is deleted)")
        return 130
    if not serving:
        say(f"done. Serve it with: {_me()} serve {dest}")
        return 0
    running = None
    try:
        running = demo.start_running(dest, config)
        return _demo_serve(dest, port, say)
    except (OSError, demo.DemoError) as exc:
        _die(f"container-map demo: {exc}", 1)
    finally:
        if running:
            demo.thread(dest, config, "stop", running)
            demo.settle(dest, config, running, timeout=10)


# ---- doctor ---------------------------------------------------------------------------------

def _floor(version=None):
    return ".".join(str(part) for part in (MIN_PYTHON if version is None else version))


def _native_windows():
    """Python on Windows itself, not WSL2 (which is Linux and reports so)."""
    return sys.platform == "win32"


def missing_pieces(version=None, which=shutil.which):
    """What the app cannot run without: the Python floor, git, and bash (serve, demo are bash)."""
    version = sys.version_info if version is None else version
    out = []
    if _native_windows():
        out.append("Linux or macOS (native Windows is not supported: use WSL2)")
    if tuple(version[:2]) < MIN_PYTHON:
        out.append(f"python {_floor()}+ (this is {version[0]}.{version[1]})")
    out += [exe for exe in ("git", "bash") if not which(exe)]
    return out


RESOLVE = """
import json, os, config
c = config.load()
print(json.dumps({"file": c["_path"], "problem": config.problem(c["_path"]),
                  "dropped": c["_dropped"], "work_root": c["work_root"], "roots": config.roots(c),
                  "out": c["out"] or os.path.join(config.HERE, "game.html"),
                  "ui_lock": c.get("ui_lock") is True}))
"""


def _row(name, value, note=""):
    print(f"  {name:<12} {value}" + (f"  ({note})" if note else ""))


def _own_bridge(root, port):
    """The live file's dict when a bridge of this map is running on `port`: $BRIDGE_LIVE_PATH or
    the file for this root and port names a running process, else the per-user file (a root that
    could not take its own) or the legacy file beside the code does AND names this root. Those two
    are shared by every map that uses them, so the last bridge to write one is not necessarily
    this root's; the bridge records its root in the file, and a file without one (a bridge from
    before it did) proves nothing. None otherwise."""
    import config
    paths = [os.environ["BRIDGE_LIVE_PATH"]] if os.environ.get("BRIDGE_LIVE_PATH") else []
    paths += [config.live_path(root, port)] if root else []
    shared = [config.user_live_path(port), config.LEGACY_LIVE]
    mine = os.path.realpath(os.path.expanduser(root)) if root else ""
    for path in paths + shared:
        info = config.live_alive(path)
        if not info or info.get("port") != port:
            continue
        if path in shared and path not in paths and not (mine and info.get("root") == mine):
            continue
        return info
    return None


def _http_status(port, timeout=5):
    """The status of GET / on the bridge's port (the page body is read and dropped), 0 when
    nothing answers."""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request("GET", "/")
            resp = conn.getresponse()
            resp.read()
            return resp.status
        finally:
            conn.close()
    except (OSError, http.client.HTTPException):
        return 0


def wait_for_bridge(live, port, before=None, seconds=None):
    """(True, "") once a bridge newer than boot id `before` has written `live` and answers 200
    on `port`; else (False, why) after `seconds` (WAIT_SECONDS): the old bridge's file, a file
    for a process that is gone and a port that answers something else all read differently."""
    import config
    end = time.monotonic() + (WAIT_SECONDS if seconds is None else seconds)
    while True:
        info = config.read_live(live)
        if info is None:
            why = f"no live file at {live}"
        elif before and info.get("boot_id") == before:
            why = f"{live} is still the old bridge's (boot {before})"
        elif info.get("port") != port:
            why = f"{live} names port {info.get('port')}, not {port}"
        elif not config.pid_alive(info.get("pid")):
            why = f"{live} names pid {info.get('pid')}, which is not running"
        else:
            status = _http_status(port)
            if status == 200:
                return True, ""
            why = (f"port {port} answered HTTP {status}, not 200" if status
                   else f"nothing answers on port {port}")
        if time.monotonic() >= end:
            return False, why
        time.sleep(0.25)


def human_accounts(platform=None, passwd="/etc/passwd", run=None):
    """Names of the people who can log in to this computer: user id 1000 and up on Linux, 501 and up on macOS, with a
    login shell (not nologin, false or empty). [] when the account list cannot be read."""
    platform = platform or _platform()
    names = []
    if platform == "darwin":
        rc, out = (run or _run)(["dscl", ".", "-readall", "/Users", "RecordName", "UniqueID", "UserShell"])
        if rc != 0:
            return []
        for block in out.split("\n-\n"):
            fields = {}
            key = ""
            for line in block.splitlines():
                if line.startswith(" ") and key:
                    fields.setdefault(key, line.strip())
                elif ":" in line:
                    key, _, rest = line.partition(":")
                    if rest.strip():
                        fields[key] = rest.strip()
            uid, shell = fields.get("UniqueID", ""), fields.get("UserShell", "")
            name = (fields.get("RecordName", "").split() or [""])[0]
            if uid.isdigit() and 501 <= int(uid) < 60000 and name and _login_shell(shell):
                names.append(name)
    else:
        try:
            with open(passwd, encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            return []
        for line in lines:
            f = line.split(":")
            if len(f) >= 7 and f[2].isdigit() and 1000 <= int(f[2]) < 60000 and _login_shell(f[6]):
                names.append(f[0])
    return sorted(set(names))


def _login_shell(shell):
    shell = shell.strip()
    return bool(shell) and os.path.basename(shell) not in ("nologin", "false", "sync", "shutdown", "halt")


def cmd_doctor(args):
    _only_flags(args, ("--port", "--config"), "doctor")
    _, env = _prepare(args, check_root=False)
    port = _port(args)
    missing = missing_pieces()
    problems = []
    v = sys.version_info
    print("container-map doctor")
    _row("version", VERSION, HERE)
    print("needed")
    _row("python", f"{v[0]}.{v[1]}.{v[2]} {PY}",
         "ok" if v[:2] >= MIN_PYTHON else f"{_floor()}+ needed")
    for exe, why in (("git", "repo facts, task worktrees"), ("bash", "serve and demo")):
        _row(exe, shutil.which(exe) or "MISSING", why)
    print("optional")
    for exe, why in (("tmux", "seats and doors"),
                     ("systemd-run", "running threads survive a restart of the map")):
        _row(exe, shutil.which(exe) or "not found", why)
    manager = {"linux": "systemctl", "darwin": "launchctl"}.get(_platform())
    _row("service", (shutil.which(manager) or f"{manager} not found") if manager
         else "none on this platform", "what `install` uses")
    offered = ()
    if v[:2] >= MIN_PYTHON:
        import tasks
        offered = [d[0] for d in tasks.DETECT]
    print("agents (tasks and the wingmate; with none the map works and tasks are off)")
    found = [exe for exe in AGENTS if shutil.which(exe)]
    for exe in AGENTS:
        path = shutil.which(exe)
        note = "offered as a task runner" if path and exe in offered else (
            "add it under \"runners\" to use it" if path else "")
        _row(exe, path or "not found", note)
    if not found:
        also = "" if _me() == "container-map" else f" (from this folder: {_me()} demo)"
        print("  no agent CLI found on PATH: the map works and tasks stay off. To see it before "
              f"you install one: container-map demo{also}")
    print("config")
    r = subprocess.run([PY, "-c", RESOLVE], capture_output=True, text=True, env=env, cwd=HERE,
                       timeout=60)
    try:
        c = json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        c = None
    root = ""
    if c is None:
        _row("config", "FAILED to load", (r.stderr.strip().splitlines() or ["no output"])[-1])
        missing.append("a loadable config")
    else:
        root = c["work_root"]
        _row("file", c["file"] or "none: zero-config defaults",
             f"UNUSABLE, {c['problem']}" if c["problem"] else "")
        if c["problem"]:
            problems.append(f"{c['file']} {c['problem']}")
        for item in c["dropped"]:      # read from the file and ignored: say which, and why
            _row("ignored", item["key"], item["reason"])
        gone = not os.path.isdir(root)
        _row("root", root, "does not exist" if gone else "")
        if gone:
            problems.append(f"root {root} does not exist: pass an existing folder "
                            f"(`{_me()} doctor ~/code`) or set work_root in container.json")
        _row("roots", ", ".join(c["roots"]) or "none")
        _row("page", c["out"])
        _row("state", os.path.join(root, ".container-map"),
             "config, page, tasks and their worktrees, history")
    import config
    live = (config.resolve_live(root=root, port=port, environ=os.environ)
            if os.path.isdir(root) or os.environ.get("BRIDGE_LIVE_PATH") else "")
    busy, taken = _port_busy(port), ""
    if not busy:
        _row("port", port, "free")
    else:
        own = _own_bridge(root, port)
        if own:
            _row("port", port, f"in use by this map's own bridge, pid {own['pid']}; stop it, or "
                 "pass --port N for a second map")
        else:
            _row("port", port, f"in use by something else: {busy}; stop it or pass --port N")
            taken = f"port {port} is held by something else: serve and install refuse it"
    if live:
        _row("live file", live, "the bridge's credentials, mode 600")
    locked = bool(c and c.get("ui_lock"))
    people = human_accounts() if not locked else []
    if locked:
        _row("ui lock", "on", "the page opens only through the link serve prints")
    elif len(people) > 1:
        _row("ui lock", "OFF", f"warning: {len(people)} people log in to this computer ({', '.join(people[:6])}"
             f"{', ...' if len(people) > 6 else ''}); any of them can open your map and start agent tasks as you. "
             f"The lock opens the page only through a link printed to you: `{_me()} serve ROOT --lock`, "
             f"`{_me()} install ROOT --lock`, or \"ui_lock\": true in container.json")
    else:
        _row("ui lock", "off", "fine for a computer only you log in to; `--lock` is for shared ones")
    parts = ([f"missing {', '.join(missing)}"] if missing else []) + problems
    if parts:
        print("not ready: " + "; ".join(parts))
        return 1
    if taken:       # not a fault of this machine (another service may own it): still ready
        print(f"note: {taken}; pass --port N")
    print(f"ready. next: {_me()} init ROOT, then {_me()} install ROOT (or serve ROOT)")
    return 0


# ---- init -----------------------------------------------------------------------------------

def planets_of(root):
    """ROOT's non-hidden top-level folders, in name order: code when it carries a repo or a
    package file, else knowledge."""
    out = []
    for name in sorted(os.listdir(root), key=str.lower):
        path = os.path.join(root, name)
        if name.startswith(".") or not os.path.isdir(path):
            continue
        code = any(os.path.exists(os.path.join(path, m)) for m in CODE_MARKERS)
        out.append({"name": name, "path": path, "kind": "code" if code else "knowledge",
                    "desc": ""})
    return out


def init_config(root, which=shutil.which):
    """container.example.json with this root's facts in place of its samples."""
    import config
    import tasks
    import wingmate
    with open(os.path.join(HERE, "container.example.json"), encoding="utf-8") as handle:
        cfg = json.load(handle)
    # the example's sample paths describe nobody's machine: clear them, keep its settings
    for key, value in list(cfg.items()):
        if isinstance(value, str) and value.startswith(("~", "/")):
            cfg[key] = ""
    cfg["_what"] = (f"Written by `container-map init` on {time.strftime('%Y-%m-%d')}. Edit freely; "
                    "every key is optional. container.example.json and "
                    "container.sources-example.json next to cli.py list the rest.")
    cfg["name"] = os.path.basename(root.rstrip("/")) or root
    cfg["work_root"] = root
    cfg["planets"] = planets_of(root)
    cfg["tmux_session"] = config.current_tmux_session()
    cfg["runners"] = tasks.detect_runners(which)
    cfg["wingmate"] = wingmate.spec_for({}, which) or {}
    return cfg


def _write(path, text, mode=0o600):
    """Atomic: a reader sees the old file or the new one, never half of it."""
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def cmd_init(args):
    _only_flags(args, ("--config", "--create"), "init")
    rest = _positional(args)
    if not rest:
        _die(f"usage: {_me()} init ROOT [--create]")
    root = os.path.abspath(os.path.expanduser(rest[0]))
    if not os.path.isdir(root):
        if os.path.exists(root) or "--create" not in args:
            _die(_not_a_dir(root, "" if os.path.exists(root) else
                            "make it first, or add --create and init makes it"), 1)
        try:
            os.makedirs(root)
        except OSError as exc:
            _die(f"container-map: cannot create {root}: {exc.strerror or exc}", 1)
        print(f"container-map: made the folder {root}")
    cfg = init_config(root)
    side = os.path.join(root, ".container-map")
    os.makedirs(side, exist_ok=True)
    import config
    config.hide_from_git(side)      # inside a repo, the map's folder is not a change to commit
    path = os.path.join(side, "container.json")
    target = path + ".new" if os.path.exists(path) else path
    _write(target, json.dumps(cfg, indent=2) + "\n")
    if target == path:
        print(f"container-map: wrote {path}")
    else:
        print(f"container-map: {path} already exists and was not touched;")
        print(f"  wrote {target} instead: compare the two and merge what you want by hand")
    kinds = {}
    for p in cfg["planets"]:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    print(f"  name {cfg['name']} · {len(cfg['planets'])} planets "
          f"({', '.join(f'{n} {k}' for k, n in sorted(kinds.items())) or 'no folders yet'})")
    print(f"  runners: {', '.join(cfg['runners']) or 'none found on PATH (tasks stay off)'}")
    mate = cfg["wingmate"]
    if mate:
        # with no claude the base command is empty and each model (codex, gemini) carries its own
        model = next((m for m in mate.get("models") or [] if m.get("name") == mate.get("default")), {})
        argv = mate.get("cmd") or model.get("cmd") or []
        mate = f"{mate['name']} on {os.path.basename(argv[0])}" if argv else f"{mate['name']} ({mate.get('model', '')})"
    else:
        mate = "none"
    print(f"  wingmate: {mate}")
    print(f"  next: {_me()} render {root}   or   {_me()} install {root}")
    return 0


# ---- install / uninstall --------------------------------------------------------------------

def _platform(platform=None):
    platform = sys.platform if platform is None else platform
    return "linux" if platform.startswith("linux") else platform


def _sd(arg, exec_line=False):
    """One systemd unit value in double quotes: C escapes, % specifiers (and $ in Exec lines)
    doubled so the path reaches the process as written."""
    arg = arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return '"' + (arg.replace("$", "$$") if exec_line else arg) + '"'


def service(platform=None, home=None, uid=None):
    """Where this platform's user service lives and how it is stopped. ValueError elsewhere."""
    platform = _platform(platform)
    explicit_home = home is not None
    home = os.path.expanduser("~") if home is None else home
    if platform == "linux":
        conf = os.path.join(home, ".config")
        if not explicit_home and os.path.isabs(os.environ.get("XDG_CONFIG_HOME", "")):
            conf = os.environ["XDG_CONFIG_HOME"]
        return {"kind": "systemd", "manager": "systemctl",
                "file": os.path.join(conf, "systemd", "user", UNIT),
                "start": [(["systemctl", "--user", "daemon-reload"], True),
                          (["systemctl", "--user", "enable", UNIT], True),
                          (["systemctl", "--user", "restart", UNIT], True)],
                "stop": [["systemctl", "--user", "disable", "--now", UNIT]],
                "after_stop": [["systemctl", "--user", "daemon-reload"]],
                "journal": ["journalctl", "--user", "-u", "container-map", "-n", "20",
                            "--no-pager"],
                "logs": "journalctl --user -u container-map -f", "log": ""}
    if platform == "darwin":
        domain = f"gui/{os.getuid() if uid is None else uid}"
        plist = os.path.join(home, "Library", "LaunchAgents", LAUNCHD_LABEL + ".plist")
        log = os.path.join(home, "Library", "Logs", "container-map.log")
        return {"kind": "launchd", "manager": "launchctl", "file": plist,
                "start": [(["launchctl", "bootout", domain, plist], False),
                          (["launchctl", "bootstrap", domain, plist], True)],
                "stop": [["launchctl", "bootout", domain, plist]], "after_stop": [],
                "journal": None, "logs": f"tail -f {log}", "log": log}
    raise ValueError(f"install knows Linux (systemd --user) and macOS (launchd), not {platform}: "
                     f"run `{_me()} serve ROOT` under your own supervisor (tmux, screen, cron "
                     f"@reboot)")


def service_plan(root, port=DEFAULT_PORT, config="", platform=None, home=None, path_env=None,
                 uid=None, lock=False):
    """service() plus the file's text: it runs `python3 cli.py serve ROOT --port N` with the PATH
    of the shell that installed it, so the agent CLIs found now are found by the service too."""
    plan = service(platform, home, uid)
    path_env = os.environ.get("PATH", "/usr/bin:/bin") if path_env is None else path_env
    argv = [PY, os.path.join(HERE, "cli.py"), "serve", root, "--port", str(port)]
    argv += ["--config", config] if config else []
    argv += ["--lock"] if lock else []
    if any("\n" in a for a in argv + [path_env]):
        raise ValueError("a path with a line break cannot go into a service file")
    if plan["kind"] == "systemd":
        plan["text"] = "\n".join([
            "[Unit]",
            f"Description=container-map: the live map of {root.replace('%', '%%')}",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={HERE.replace('%', '%%')}",
            f"Environment={_sd('PATH=' + path_env)}",
            "ExecStart=" + " ".join(_sd(a, exec_line=True) for a in argv),
            "Restart=on-failure",
            "RestartSec=5",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ])
    else:
        plan["text"] = plistlib.dumps({
            "Label": LAUNCHD_LABEL, "ProgramArguments": argv, "WorkingDirectory": HERE,
            "EnvironmentVariables": {"PATH": path_env}, "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 10,
            "StandardOutPath": plan["log"], "StandardErrorPath": plan["log"],
        }, sort_keys=False).decode("utf-8")
    return plan


def _retired(path):
    """A free name to rename path to: rename() over an existing file would destroy it."""
    base = f"{path}.retired-{time.strftime('%Y%m%d-%H%M%S')}"
    name, n = base, 1
    while os.path.lexists(name):
        n += 1
        name = f"{base}-{n}"
    return name


def cmd_install(args, platform=None, home=None, run=None, which=None):
    run, which = run or _run, which or shutil.which
    _only_flags(args, ("--port", "--dry-run", "--config", "--lock"), "install")
    rest = _positional(args)
    if not rest:
        _die(f"usage: {_me()} install ROOT [--port N] [--dry-run]")
    root = os.path.abspath(os.path.expanduser(rest[0]))
    if not os.path.isdir(root):
        _die(f"container-map: not a directory: {root}", 1)
    port = _port(args)
    try:
        plan = service_plan(root, port, _config_arg(args), platform, home, lock="--lock" in args)
    except ValueError as exc:
        _die(f"container-map: {exc}")
    path = plan["file"]
    if "--dry-run" in args:
        print(f"# {path}  (dry run: nothing written, nothing started)")
        print(plan["text"], end="")
        print("# then: " + "; ".join(" ".join(a) for a, _ in plan["start"]))
        return 0
    if not which(plan["manager"]):
        _die(f"container-map: {plan['manager']} not found, so no {plan['kind']} service here; "
             f"run `{_me()} serve {root}` under your own supervisor")
    busy = _port_busy(port)
    if busy and not _own_bridge(root, port):
        _die(f"container-map: port {port} on 127.0.0.1 is held by something else ({busy}); "
             f"nothing was written. Stop what holds it or pass --port N (a map you installed "
             f"earlier for another folder? `{_me()} uninstall` first)", 1)
    import config
    # where the service's bridge will put its credentials (it picks the same way)
    live = config.default_live_path({"work_root": root}, port)
    before = (config.read_live(live) or {}).get("boot_id")     # the old bridge's, if any
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if plan["log"]:
        os.makedirs(os.path.dirname(plan["log"]), exist_ok=True)
    old = None
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as handle:
            old = handle.read()
    if old is not None and old != plan["text"]:
        moved = _retired(path)
        os.rename(path, moved)
        print(f"container-map: the previous {os.path.basename(path)} is kept as {moved}")
    if old != plan["text"]:
        _write(path, plan["text"], 0o644)
    for argv, required in plan["start"]:
        rc, out = run(argv)
        if rc != 0 and required:
            print(f"container-map: `{' '.join(argv)}` failed (exit {rc}): {out}", file=sys.stderr)
            print(f"  {path} is written; start the map by hand instead: {_me()} serve {root}",
                  file=sys.stderr)
            return 1
    # the manager returns as soon as it has forked the service: started is not up
    ok, why = wait_for_bridge(live, port, before)
    if not ok:
        print(f"container-map: the service was started but no bridge came up on port {port} "
              f"within {WAIT_SECONDS:g} s: {why}", file=sys.stderr)
        tail = _service_log(plan, run)
        if tail:
            print("  the service's last log lines:", file=sys.stderr)
            print("\n".join("    " + line for line in tail.splitlines()), file=sys.stderr)
        print("  a big root is rendered once before the bridge starts, so the service may still "
              f"come up in a moment: watch with `{plan['logs']}`", file=sys.stderr)
        print(f"  the unit stays at {path}. To see it fail in the foreground: {_me()} serve "
              f"{root} --port {port}. To remove the service: {_me()} uninstall", file=sys.stderr)
        return 1
    print(f"container-map: installed and started ({plan['kind']}): {path}")
    key = (config.read_live(live) or {}).get("launch_key")
    if isinstance(key, str) and key:       # locked: the link goes to this terminal, not to the service's log
        print(f"  locked. Open this link (private to you; `{_me()} link --port {port}` shows it again):\n"
              f"  {_launch_link(port, key)}")
        print(f"  logs: {plan['logs']}   stop: {_me()} uninstall")
    else:
        print(f"  live at http://127.0.0.1:{port}/   logs: {plan['logs']}   stop: {_me()} uninstall")
    return 0


def _service_log(plan, run, lines=20):
    """The last lines of what the service wrote: the journal (systemd) or its log file (launchd)."""
    if plan.get("journal"):
        rc, out = run(plan["journal"])
        return out if out else f"(the journal gave nothing: exit {rc})"
    try:
        with open(plan["log"], encoding="utf-8", errors="replace") as handle:
            return "\n".join(handle.read().splitlines()[-lines:])
    except OSError as exc:
        return f"(no log to read: {exc.strerror or exc})"


# what a service manager says about a service that is not running, or was never loaded: that is
# stopped, not a failure (a bus that cannot be reached says something else, and is one)
GONE = ("not loaded", "does not exist", "no such process", "could not find")


def cmd_uninstall(args, platform=None, home=None, run=None, which=None):
    run, which = run or _run, which or shutil.which
    _only_flags(args, ("--dry-run", "--force"), "uninstall")
    try:
        svc = service(platform, home)
    except ValueError as exc:
        _die(f"container-map: {exc}")
    path = svc["file"]
    if not os.path.exists(path):
        print(f"container-map: nothing installed at {path}")
        return 0
    moved = _retired(path)
    if "--dry-run" in args:
        print(f"# dry run: would run {'; '.join(' '.join(a) for a in svc['stop'])}")
        print(f"# then rename {path} -> {moved} (nothing is deleted)")
        return 0
    failed = []
    if which(svc["manager"]):
        for argv in svc["stop"]:
            rc, out = run(argv)
            if rc != 0 and not any(text in (out or "").lower() for text in GONE):
                failed.append(f"`{' '.join(argv)}` exit {rc}: {out}")
    else:
        failed.append(f"{svc['manager']} not found: nothing was stopped")
    if failed and "--force" not in args:     # the file stays, so a retry still finds what to stop
        print("container-map: the service could not be stopped, so its file is kept at "
              f"{path}: " + "; ".join(failed), file=sys.stderr)
        print(f"  stop it by hand, then run `{_me()} uninstall` again. If the service never "
              f"started (install failed before it was loaded), `{_me()} uninstall --force` "
              f"retires {path} anyway", file=sys.stderr)
        return 1
    if failed:
        print("container-map: --force: could not stop the service, retiring its file anyway: "
              + "; ".join(failed), file=sys.stderr)
    os.rename(path, moved)
    for argv in svc["after_stop"]:
        run(argv)
    print(f"container-map: retired {path} -> {moved} (nothing deleted)")
    return 0


# ---- thread: the thread engine without a browser --------------------------------------------

THREAD_USAGE = """container-map thread: hand work to an agent and land it, no browser needed

  thread new TARGET "text" --agent NAME [--model M] [--verify "line"] [--retries N]
             [--setup "line"] [--in-place] [--wait]      start a thread; prints its id
  thread list [--open]                                   every thread, newest first
  thread show ID [--run N]                               its summary and a run's log
  thread diff ID                                         the patch against where it started
  thread reply ID "text" [--wait]                        a follow-up in the same folder
  thread check ID --agent NAME [--model M] [--wait]      a read-only review; prints the verdict
  thread stop ID                                         end the running agent
  thread merge ID [--force]                              land it (refused, exit 2, on bad evidence)
  thread aside ID                                        keep the work on its branch, drop the tree
  thread done ID                                         close a thread that worked in place

  Every subcommand takes --json (one JSON object on stdout), --config FILE and --root DIR (the
  same as serve's, so the CLI and a running map share one task store).
  --wait follows the run and runs the oracle loop (verify retries) itself: no bridge needed.
  Exit: 0 ok, review or PASS; 1 failed, stopped, lost, not PASS, or the store refused;
        2 usage, or merge refused by the evidence; 130 interrupted (the thread keeps running).
  Reference: docs/CLI.md
"""
THREAD_COMMON = (("--config", "--root"), ("--json",))
THREAD_SPECS = {           # subcommand: (positional names, value flags, switch flags)
    "new": (("TARGET", "TEXT"), ("--agent", "--model", "--verify", "--retries", "--setup"),
            ("--in-place", "--wait")),
    "list": ((), (), ("--open",)),
    "show": (("ID",), ("--run",), ()),
    "diff": (("ID",), (), ()),
    "reply": (("ID", "TEXT"), (), ("--wait",)),
    "check": (("ID",), ("--agent", "--model"), ("--wait",)),
    "stop": (("ID",), (), ()),
    "merge": (("ID",), (), ("--force",)),
    "aside": (("ID",), (), ()),
    "done": (("ID",), (), ()),
}
POLL_SECONDS = 0.5         # --wait reads the log and the receipts this often
TICK_SECONDS = 3.0         # and runs the oracle loop (store.tick) this often
LOST_GRACE = 2             # polls a "lost" must persist before --wait believes it (see _follow)
LOG_KEYS = ("run", "offset", "size", "more", "lines", "prompts", "verify_tail", "setup_tail",
            "closed_note")
BUSY = ("new", "running", "checking", "stopping")
KIND_MARK = {"meta": "# ", "tool": "> ", "cmd": "> ", "file": "> ", "err": "! ", "result": "= ",
             "think": "~ ", "info": "· "}


class Refused(Exception):
    """A thread command that cannot go on: its exit code and the line that says why."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _thread_args(sub, args):
    """(positionals, {flag: value or True}). `--` ends the flags, so a text that starts with --
    still gets through; anything else unknown is a usage error."""
    names, values, switches = THREAD_SPECS[sub]
    values, switches = values + THREAD_COMMON[0], switches + THREAD_COMMON[1]
    pos, opts, i, ended = [], {}, 0, False
    while i < len(args):
        a = args[i]
        if ended or not a.startswith("--"):
            pos.append(a)
        elif a == "--":
            ended = True
        else:
            key, eq, value = a.partition("=")
            if key in switches and not eq:
                opts[key] = True
            elif key in values:
                if not eq:
                    i += 1
                    if i >= len(args):
                        raise Refused(2, f"{key} needs a value")
                    value = args[i]
                opts[key] = value
            else:
                raise Refused(2, f"unknown option {a} (takes "
                                 f"{', '.join(values + switches)})")
        i += 1
    if len(pos) != len(names):
        flags = " ".join(f"[{v} …]" for v in values) + " " + " ".join(f"[{s}]" for s in switches)
        raise Refused(2, f"usage: {_me()} thread {sub} {' '.join(names)} {flags}".rstrip())
    return pos, opts


def _int_opt(opts, name, default):
    raw = opts.get(name)
    if raw in (None, ""):
        return default
    if not str(raw).isdigit():
        raise Refused(2, f"{name} takes a whole number, got {raw!r}")
    return int(raw)


# what makes a folder a map: the config `init` wrote, or what a zero-config `serve` made there
MAP_MARKERS = ("container.json", "tasks", "run")


def _thread_config(opts, target=None):
    """config.load() resolved the way serve resolves it: --config FILE, else $CONTAINER_CONFIG,
    else ROOT/.container-map/container.json for --root ROOT (or $CONTAINER_ROOT), else the
    nearest folder above the thread's TARGET (`new`) or above the current folder that holds a
    .container-map with a container.json, or with tasks or a run folder (a map served without
    init), else the tool's container.json. Same file, same tasks_dir: the map and the CLI work
    on one store."""
    import config
    root, path = opts.get("--root") or "", opts.get("--config") or ""
    if root:
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(root):
            raise Refused(1, f"not a directory: {root}")
    if path:
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(path):
            raise Refused(1, f"no such config file: {path}")
    if not (root or path or os.environ.get("CONTAINER_CONFIG") or os.environ.get("CONTAINER_ROOT")):
        for start in ([target] if target else []) + [os.getcwd()]:
            root = config.find_root(start, MAP_MARKERS)
            if root:
                break
    saved = os.environ.get("CONTAINER_ROOT")
    try:
        if root:
            os.environ["CONTAINER_ROOT"] = root    # config reads the root from here (or argv)
        return config.load(path or None)
    finally:
        if root and saved is None:
            os.environ.pop("CONTAINER_ROOT", None)
        elif root:
            os.environ["CONTAINER_ROOT"] = saved


def thread_store(opts, target=None):
    """The bridge's own store for this config (tasks.store_for), roots as the bridge has them."""
    import config
    import tasks
    cfg = _thread_config(opts, target)
    roots = tuple(config.task_roots(cfg)) or (os.path.expanduser("~/work"),)   # never file_roots
    store = tasks.store_for(cfg, roots)
    store.config_file = cfg["_path"]       # for the messages that say which map was meant
    return store


def _summary(got):
    """The thread's summary out of a store.get() (which adds the log to it) or a close()."""
    return {k: v for k, v in got.items() if k not in LOG_KEYS and k != "message"}


def _offered(store, role):
    """The runners whose role is not `role`: "check" names who can work, "work" who can check."""
    names = [n for n, s in store.runners.items() if s.get("role", "both") != role]
    return f"offered: {', '.join(names)}" if names else "no agents are configured or installed"


def _say(as_json, payload, text):
    if as_json:
        print(json.dumps(payload), flush=True)
    elif text:
        print(text, flush=True)


def _print_line(line, stream):
    print(KIND_MARK.get(line.get("k"), "") + str(line.get("s", "")), file=stream, flush=True)


def _status_line(t):
    bits = [t.get("status", "?")]
    if t.get("rc") is not None:
        bits.append(f"agent exit {t['rc']}")
    if t.get("verify_cmd"):
        bits.append(f"attempt {t.get('attempts', 0)} of {1 + int(t.get('retries') or 0)}")
    if t.get("retry_due"):
        bits.append("retry due: a running map or a --wait sends it")
    if t.get("blocked"):
        bits.append(f"retry blocked: {t['blocked']}")
    return f"{t['id']}: " + " · ".join(bits)


def _verify_line(t):
    import tasks
    v = t.get("verify") or {}
    if not t.get("verify_cmd"):
        return ""
    if v.get("rc") is None:
        return f"verify  `{t['verify_cmd']}`: has not run"
    if v.get("timed_out"):        # the cap ended it: say so, not a bare exit -15
        return (f"verify  `{t['verify_cmd']}`: timed out after {tasks.secs_text(v.get('limit'))} s "
                f"(ended, exit {v['rc']}; run {v['n']})")
    return f"verify  `{t['verify_cmd']}`: exit {v['rc']} in {v.get('seconds')}s (run {v['n']})"


def _evidence_lines(t, verify_tail=None):
    """The verify receipt (with its output's tail when it failed), the check, the blockers."""
    out = [x for x in (_verify_line(t),) if x]
    v = t.get("verify") or {}
    if verify_tail and v.get("rc") not in (None, 0):
        out += ["  " + x for x in verify_tail.strip().splitlines() if x.strip()][-8:]
    s = t.get("setup") or {}
    if t.get("setup_cmd") and s.get("rc") not in (None, 0):
        import tasks
        how = (f"timed out after {tasks.secs_text(s.get('limit'))} s (ended, exit {s['rc']})"
               if s.get("timed_out") else f"exit {s['rc']}")
        out.append(f"setup   `{t['setup_cmd']}`: {how} (run {s['n']}); no agent ran")
    c = t.get("check")
    if c:
        out.append(f"check   by {c.get('runner')}: {c.get('verdict')} (run {c.get('n')}"
                   + (", stale: work came after it)" if c.get("stale") else ")"))
    if t.get("evidence"):
        out.append(f"evidence {t['evidence']}")
    if t.get("merge_blockers"):
        out.append("merge blockers: " + "; ".join(t["merge_blockers"]))
    return out


def _final_text(t, verify_tail):
    out = [_status_line(t)] + _evidence_lines(t, verify_tail)
    tid = t["id"]
    if t.get("status") == "review":
        out.append(f"next: {_me()} thread diff {tid}  (then check --agent NAME, merge)")
    elif t.get("status") in ("failed", "stopped", "lost"):
        out.append(f"next: {_me()} thread show {tid}  (then reply \"…\", or aside)")
    return "\n".join(out)


def _work_settled(t):
    """--wait's exit code once the thread's own work is over: review 0; failed, stopped, lost 1
    (a verify retry that is blocked, because its agent or worktree is gone, ends it as failed).
    None while it runs, is being checked, or has a verify retry due (the tick sends it)."""
    if t["status"] in BUSY or t.get("retry_due"):
        return None
    return 0 if t["status"] in ("review", "merged", "done") else 1


def _check_settled(n):
    def settled(t):
        c = t.get("check") or {}
        if (c.get("n") or 0) < n or c.get("verdict") in ("checking", "stopping"):
            return None
        return 0 if c.get("verdict") == "PASS" else 1
    return settled


def _follow(store, tid, settled, stream):
    """Stream the thread's newest run to `stream` and run the oracle loop (store.tick) every
    TICK_SECONDS until settled(summary) gives an exit code. Returns (code, last get(), the last
    VERDICT: line seen). Ctrl-C stops the waiting, never the thread."""
    import tasks
    run, offset, lost, next_tick, verdict = None, 0, 0, 0.0, None
    try:
        while True:
            got = store.get(tid, run, offset)
            for line in got["lines"]:
                _print_line(line, stream)
                found = [x for x in str(line.get("s", "")).splitlines()
                         if tasks.VERDICT_RE.search(x)]
                verdict = found[-1].strip() if found else verdict
            moved = got["offset"] > offset
            run, offset = got["run"], got["offset"]
            if got["more"] and moved:
                continue                        # more of this run is on disk already
            if got["runs"] > run:               # a retry, a reply or a check began: follow it
                run, offset = got["runs"], 0
                continue
            code = settled(got)
            # the supervisor writes its exit receipt and then exits: a read between our look at
            # the events and our look at its pid says "lost" for a moment, so it must persist
            check = got.get("check") or {}
            if code is not None and "lost" in (got["status"], check.get("verdict")):
                lost += 1
                code = code if lost > LOST_GRACE else None
            else:
                lost = 0
            if code is not None:
                return code, got, verdict
            if time.monotonic() >= next_tick:
                next_tick = time.monotonic() + TICK_SECONDS
                try:
                    retried = store.tick()
                except Exception as exc:       # a broken retry is reported, never waited on
                    raise Refused(1, f"the oracle loop failed: {type(exc).__name__}: {exc}")
                if tid in retried:
                    print(f"· the verify failed; {tid} goes back to its agent", file=stream,
                          flush=True)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        raise Refused(130, f"stopped waiting; {tid} keeps running: {_me()} thread show {tid}")


def _wait_work(store, tid, as_json):
    stream = sys.stderr if as_json else sys.stdout
    code, got, _ = _follow(store, tid, _work_settled, stream)
    t = _summary(got)
    _say(as_json, {"ok": code == 0, "id": tid, "thread": t, "waited": True,
                   "verify_tail": got.get("verify_tail")}, _final_text(t, got.get("verify_tail")))
    return code


def _t_new(store, pos, opts, as_json):
    import tasks
    target, text = pos
    if not opts.get("--agent"):
        raise Refused(2, f"pick the agent with --agent NAME "
                         f"({_offered(store, 'check')})")
    try:
        t = store.create(text, os.path.abspath(os.path.expanduser(target)), opts["--agent"],
                         not opts.get("--in-place"), opts.get("--model") or None,
                         opts.get("--verify") or None, _int_opt(opts, "--retries", 0),
                         opts.get("--setup") or None)
    except tasks.TaskError as exc:
        if exc.status == 403 and "roots" in exc.message:
            exc.message += (f" ({', '.join(store.roots)}, from "
                            f"{getattr(store, 'config_file', '') or 'the built-in defaults'}; "
                            "--root or --config picks another map)")
        raise
    tid = t["id"]
    if opts.get("--setup") and t.get("mode") != "worktree":
        print("container-map thread: --setup runs only in a thread's own worktree; this one works "
              "in place, so it was skipped", file=sys.stderr)
    if not opts.get("--wait"):
        _say(as_json, {"ok": True, "id": tid, "thread": t, "waited": False, "verify_tail": None},
             tid)
        return 0
    print(tid if not as_json else f"thread {tid}", file=sys.stderr if as_json else sys.stdout,
          flush=True)
    return _wait_work(store, tid, as_json)


def _t_list(store, pos, opts, as_json):
    import tasks
    rows = store.list()
    if opts.get("--open"):
        rows = [t for t in rows if t.get("status") not in tasks.CLOSED]
    wide = max([len(t["id"]) for t in rows] or [0])
    text = "\n".join(f"{t['id']:<{wide}}  {t.get('status', '?'):<9} "
                     f"{str(t.get('runner') or '-'):<10} {t.get('title', '')}" for t in rows)
    _say(as_json, {"ok": True, "threads": rows, "agents": tasks.public_runners(store.runners),
                   "max_running": store.max_running},
         text or ("no open threads" if opts.get("--open") else "no threads"))
    return 0


def _t_show(store, pos, opts, as_json):
    tid, run = pos[0], _int_opt(opts, "--run", None)
    got = store.get(tid, run, 0)
    lines = list(got["lines"])
    while got["more"]:                          # the whole log, a chunk at a time
        nxt = store.get(tid, got["run"], got["offset"])
        if nxt["offset"] <= got["offset"]:
            break
        lines += nxt["lines"]
        got = nxt
    t = _summary(got)
    prompt = next((p for p in got["prompts"] if p.get("n") == got["run"]), None)
    if as_json:
        _say(True, {"ok": True, "id": tid, "thread": t, "run": got["run"], "lines": lines,
                    "prompts": got["prompts"], "verify_tail": got["verify_tail"],
                    "setup_tail": got["setup_tail"], "closed_note": got["closed_note"]}, "")
        return 0
    where = (f"worktree on {t.get('branch')} from {t.get('base_branch') or 'a detached HEAD'}"
             if t.get("mode") == "worktree" else "in place")
    out = [_status_line(t), f"title   {t.get('title', '')}",
           f"agent   {t.get('runner')}" + (f" · model {t['model']}" if t.get("model") else ""),
           f"folder  {t.get('cwd')} · {where}"] + _evidence_lines(t, got["verify_tail"])
    if prompt:
        how = ("check" if prompt.get("kind") == "check" else "auto retry" if prompt.get("auto")
               else "reply" if prompt.get("followup") else "task")
        first = (prompt.get("text") or "").strip().splitlines()[:1]
        out.append(f"--- run {got['run']} ({how}): {first[0][:80] if first else ''}")
    else:
        out.append(f"--- run {got['run']}")
    print("\n".join(out), flush=True)
    for line in lines:
        _print_line(line, sys.stdout)
    return 0


def _t_diff(store, pos, opts, as_json):
    tid = pos[0]
    d = {"files": [], "patch": "", "truncated": False, "note": "", "where": None}
    d.update(store.diff(tid))
    if as_json:
        _say(True, {"ok": True, "id": tid, **d}, "")
        return 0
    if d["patch"]:
        sys.stdout.write(d["patch"] if d["patch"].endswith("\n") else d["patch"] + "\n")
    else:
        for f in d["files"]:
            print(f"{f.get('status', 'M')}  {f.get('path')}")
        print(d["note"] or "no changes", file=sys.stderr if d["files"] else sys.stdout)
    if d["truncated"]:
        print("container-map thread: the patch was cut at its size limit", file=sys.stderr)
    return 0


def _t_reply(store, pos, opts, as_json):
    tid, text = pos
    t = store.followup(tid, text)
    if opts.get("--wait"):
        return _wait_work(store, tid, as_json)
    _say(as_json, {"ok": True, "id": tid, "thread": t, "waited": False, "verify_tail": None},
         f"{tid}: {t['status']} (run {t['runs']})")
    return 0


def _t_check(store, pos, opts, as_json):
    tid = pos[0]
    if not opts.get("--agent"):
        raise Refused(2, f"pick the checker with --agent NAME "
                         f"({_offered(store, 'work')})")
    t = store.check(tid, opts["--agent"], opts.get("--model") or None)
    n = (t.get("check") or {}).get("n") or t["runs"]
    if not opts.get("--wait"):
        _say(as_json, {"ok": True, "id": tid, "thread": t, "run": n, "waited": False,
                       "verdict": None, "reason": None},
             f"{tid}: checking with {opts['--agent']} (run {n}); "
             f"{_me()} thread show {tid} --run {n} reads it")
        return 0
    stream = sys.stderr if as_json else sys.stdout
    code, got, reason = _follow(store, tid, _check_settled(n), stream)
    t = _summary(got)
    verdict = (t.get("check") or {}).get("verdict")
    _say(as_json, {"ok": code == 0, "id": tid, "thread": t, "run": n, "waited": True,
                   "verdict": verdict, "reason": reason},
         f"{tid}: check by {opts['--agent']}: {verdict}" + (f"\n{reason}" if reason else ""))
    return code


def _t_stop(store, pos, opts, as_json):
    t = store.stop(pos[0])
    _say(as_json, {"ok": True, "id": t["id"], "thread": t}, f"{t['id']}: {t['status']}")
    return 0


def _t_merge(store, pos, opts, as_json):
    tid, force = pos[0], bool(opts.get("--force"))
    t = _summary(store.get(tid))
    blockers = t.get("merge_blockers") or []
    # refuse here only where close() itself would get as far as the evidence; anything it
    # refuses earlier (still running, closed, worked in place) it says itself, exit 1
    landable = t["status"] not in BUSY + ("merged", "discarded", "done")
    if blockers and not force and landable and t.get("mode") == "worktree":
        _say(as_json, {"ok": False, "id": tid, "thread": t, "merged": False, "forced": False,
                       "merge_blockers": blockers, "message": "refused: the evidence says not yet"},
             f"{tid}: not merged, the evidence says not yet:\n"
             + "\n".join(f"  - {b}" for b in blockers)
             + f"\nmerge anyway: {_me()} thread merge {tid} --force")
        return 2
    res = store.close(tid, "merge", force)
    t = _summary(res)
    landed = t["status"] == "merged"
    message = res.get("message", "")
    if landed and force and blockers:
        message += f" (forced past: {'; '.join(blockers)})"
    _say(as_json, {"ok": True, "id": tid, "thread": t, "merged": landed,
                   "forced": bool(force and blockers), "merge_blockers": blockers,
                   "message": message}, f"{tid}: {message}")
    return 0


def _t_close(action):
    def run(store, pos, opts, as_json):
        res = store.close(pos[0], action)
        message = res.get("message") or res["status"]
        _say(as_json, {"ok": True, "id": res["id"], "thread": _summary(res), "message": message},
             f"{res['id']}: {message}")
        return 0
    return run


THREAD_COMMANDS = {"new": _t_new, "list": _t_list, "show": _t_show, "diff": _t_diff,
                   "reply": _t_reply, "check": _t_check, "stop": _t_stop, "merge": _t_merge,
                   "aside": _t_close("discard"), "done": _t_close("done")}


def _thread_failed(as_json, code, message, sub=""):
    if as_json:
        print(json.dumps({"ok": False, "error": message, "exit": code}), flush=True)
    else:
        print(f"container-map thread{' ' + sub if sub else ''}: {message}", file=sys.stderr,
              flush=True)
    return code


def cmd_thread(args):
    head = args[:args.index("--")] if "--" in args else args
    as_json = "--json" in head
    if not args or args[0] in ("-h", "--help", "help"):
        print(THREAD_USAGE)
        return 0
    if sys.version_info[:2] < MIN_PYTHON:
        return _thread_failed(as_json, 1, f"threads need python {_floor()}+")
    import tasks
    sub = args[0] if args[0] in THREAD_SPECS else ""
    try:
        if not sub:
            raise Refused(2, f"unknown thread command {args[0]!r} "
                             f"(one of {', '.join(THREAD_SPECS)}; {_me()} thread --help)")
        pos, opts = _thread_args(sub, args[1:])
        store = thread_store(opts, pos[0] if sub == "new" else None)
        return THREAD_COMMANDS[sub](store, pos, opts, as_json)
    except Refused as exc:
        return _thread_failed(as_json, exc.code, exc.message, sub)
    except tasks.TaskError as exc:
        return _thread_failed(as_json, 1, exc.message, sub)
    except (OSError, ValueError) as exc:          # the store's files, as the bridge reports them
        return _thread_failed(as_json, 1, f"task store: {exc}", sub)


# ---- phone access: pair and revoke through the running map ------------------------------------

def _launch_key(port):
    """The launch key of the locked map on `port`, from its live file when that file is private to this user; else ''."""
    import config
    try:
        live = config.resolve_live("", port)
        info = config.read_live(live)
        if not info or info.get("port") != port or os.stat(live).st_mode & 0o077:
            return ""
    except (RuntimeError, OSError):
        return ""
    key = info.get("launch_key")
    return key if isinstance(key, str) else ""


def _phone_call(cmd, port, method, path, body=None):
    """(status, JSON) of one call to the running map over loopback, made the way its page makes
    it: the UI session cookie GET / hands every loopback caller, then the request with the
    map's own origin. Pairing codes come from nowhere else, and never with the agent token."""
    origin = f"http://127.0.0.1:{port}"
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        conn.request("GET", "/")
        first = conn.getresponse()
        page = first.read()
        conn.close()
        cookie = (first.getheader("Set-Cookie") or "").split(";", 1)[0]
        opened = False
        if first.status == 403 and not cookie:       # a locked map: its launch key is in its 0600 live file
            key = _launch_key(port)
            if not key:
                _die(f"container-map {cmd}: the map on 127.0.0.1:{port} is locked and its launch key was not "
                     "found; run this from the map's folder (or set BRIDGE_LIVE_PATH), as the user who started it", 1)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            conn.request("GET", "/?k=" + key)
            first = conn.getresponse()
            page = first.read()
            conn.close()
            cookie = (first.getheader("Set-Cookie") or "").split(";", 1)[0]
            if first.status == 303 and cookie:
                opened = True
        if (first.status != 200 and not opened) or not cookie:
            try:
                said = json.loads(page.decode("utf-8")).get("error", "")
            except (ValueError, AttributeError):
                said = ""
            _die(f"container-map {cmd}: the map on 127.0.0.1:{port} answered {first.status} "
                 "to GET /" + (f": {said}" if said else ""), 1)
        headers = {"Cookie": cookie, "Origin": origin}
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        conn.request(method, path, body=payload, headers=headers)
        reply = conn.getresponse()
        data = reply.read()
        conn.close()
    except (OSError, http.client.HTTPException) as exc:
        why = getattr(exc, "strerror", None) or exc
        _die(f"container-map {cmd}: no map answers on 127.0.0.1:{port} ({why}); "
             f"start it with `{_me()} serve` or pass --port N", 1)
    try:
        value = json.loads(data.decode("utf-8"))
    except ValueError:
        value = {"error": data[:200].decode("utf-8", "replace")}
    return reply.status, value


def cmd_pair(args):
    _only_flags(args, ("--port", "--json", "--hours"), "pair")
    if _positional(args):
        _die("container-map pair: takes no arguments (usage: pair [--hours N] [--port N] [--json])")
    body = {}
    if any(a == "--hours" or a.startswith("--hours=") for a in args):
        raw = _flag(args, "--hours")
        if not (raw or "").isdigit() or not 1 <= int(raw) <= 168:
            _die("container-map pair: --hours takes a whole number from 1 to 168 (a week)")
        body = {"hours": int(raw)}
    status, got = _phone_call("pair", _port(args), "POST", "/phone/pair", body)
    if status != 200:
        _die(f"container-map pair: {got.get('error', status)}", 1)
    if "--json" in args:
        print(json.dumps(got))
        return 0
    code = str(got["code"])
    print(f"open  {got['url']}  on the phone")
    seconds = int(got["expires_in"])
    left = f"{seconds // 60} minutes" if seconds < 7200 else f"{seconds // 3600} hours"
    print(f"code  {code[:4]} {code[4:]}   (one use, expires in {left})")
    return 0


def cmd_devices(args):
    _only_flags(args, ("--port", "--json", "--revoke"), "devices")
    if _positional(args):
        _die("container-map devices: takes no arguments "
             "(usage: devices [--revoke ID] [--port N] [--json])")
    port = _port(args)
    if any(a == "--revoke" or a.startswith("--revoke=") for a in args):
        ident = _flag(args, "--revoke")
        if not ident:
            _die("container-map devices: --revoke needs a device id (see `devices`)")
        status, got = _phone_call("devices", port, "POST", "/phone/revoke", {"id": ident})
        if status != 200:
            _die(f"container-map devices: {got.get('error', status)}", 1)
        print(json.dumps(got) if "--json" in args else f"revoked {ident}")
        return 0
    status, got = _phone_call("devices", port, "GET", "/phone")
    if status != 200:
        _die(f"container-map devices: {got.get('error', status)}", 1)
    if "--json" in args:
        print(json.dumps(got))
        return 0
    if got.get("enabled"):
        state = "listening" if got.get("listening") else "NOT listening"
        print(f"phone access: {got.get('public_origin')} ({state})")
        if got.get("error"):
            print(f"  {got['error']}")
    else:
        print("phone access: off" + (f" ({got['error']})" if got.get("error") else ""))
    devices = got.get("devices") or []
    if not devices:
        print("no phone is paired")
    for d in devices:
        paired, seen = str(d["created"])[:10], str(d["last_seen"])[:16].replace("T", " ")
        print(f"  {d['id']}  {d['name']:<20}  paired {paired}  seen {seen}")
    return 0


# ---- workflows: `container-map workflow` (alias `wf`), `show` and `area set` ---------------------
# A workflow is a few steps, each where it works (a folder on the map, a machine, or a named place), that any
# agent puts on the map and reports as it goes; the map draws it live. `wf` works on the same store as
# the running map (workflows.py, beside the threads) without the bridge; `show` paints through the running map
# (shows live in its memory). Exit: 0 ok, 1 refused (the reason on stderr), 2 usage, 130 interrupted.

WORKFLOW_USAGE = """container-map workflow (alias wf): put a few steps on the map and watch them happen

  wf new SPEC.json|- [--plan] [--wait] [--json] [--token-file PATH]   prints the id and the token (once)
  wf new "Title" --step "Label@/path" [--step "Label@place:docs site" ...] [--by NAME]
                                         quick form: note steps in a chain
  wf step [ID] KEY --state working|done|failed|skipped [--says T] [--progress 120/800] [--output PATH|TEXT]
  wf pulse [ID] [KEY]                    still alive (always exits 0: safe in a hook)
  wf done [ID] · wf stop [ID] [--why T] · wf list · wf show [ID] [--wait] [--json]
  wf start ID · wf gate ID KEY --pass|--fail [--why T]   yours: refused without a terminal
  wf new --from ID                       Run again: a planned copy of a finished workflow (yours)
  wf new --template NAME --at PATH --goal "one sentence" [--runner R]   yours: a planned workflow
  wf templates · wf save ID --as NAME    the three built-in templates and yours; keep one (yours)
  wf wrap [--title T] [--at PATH] -- CMD...   CMD as a one-step workflow: working, then done or failed; on a
                                         terminal CMD is the foreground job (Ctrl-C, /dev/tty prompts work)
  wf demo                                a 4-step workflow on this map (for a look, probes, screenshots)
  wf schema [--json]                     the spec, the show ops and the bounds, for any agent
  wf retire --older 30d [--attic DIR]    move closed workflows to an attic folder (MANIFEST + UNDO)

  ID and the token come from $CM_WORKFLOW / $CM_WORKFLOW_TOKEN, or from --token-file PATH (mode 600,
  written by `wf new --token-file PATH`: a session whose shell forgets its variables between calls).
  Every subcommand takes --config FILE and --root DIR (the same as serve's: one store with the map).
  Exit: 0 ok; 1 refused (the reason on stderr), or a workflow --wait saw fail or stop; 2 usage;
        130 interrupted (a --wait stops waiting, the workflow goes on; a wrap stops its command, the step fails).
  Reference: docs/CLI.md and docs/AGENT-API.md
"""
WF_COMMON = (("--config", "--root", "--token-file"), ("--json",))
WORKFLOW_SPECS = {      # subcommand: (fewest positionals, most, value flags, switch flags, flags that repeat)
    "new": (0, 1, ("--by", "--step", "--from", "--template", "--at", "--goal", "--runner"), ("--plan", "--wait"),
            ("--step",)),
    "step": (1, 2, ("--state", "--says", "--progress", "--output"), (), ()),
    "pulse": (0, 2, (), (), ()),
    "done": (0, 1, (), (), ()),
    "stop": (0, 1, ("--why",), ("--tasks",), ()),
    "list": (0, 0, (), (), ()),
    "show": (0, 1, (), ("--wait",), ()),
    "start": (1, 1, (), (), ()),
    "gate": (2, 2, ("--why",), ("--pass", "--fail"), ()),
    "wrap": (0, 0, ("--title", "--at", "--by"), (), ()),
    "demo": (0, 0, ("--by",), (), ()),
    "schema": (0, 0, (), (), ()),
    "retire": (0, 0, ("--older", "--attic"), (), ()),
    "templates": (0, 0, (), (), ()),
    "save": (1, 1, ("--as",), (), ()),
}
WF_MARK = {"done": "✓", "working": "▸", "needs_you": "!", "failed": "✗", "skipped": "–", "blocked": "⊘",
           "waiting": "·", "ready": "○"}
WF_WORD = {"needs_you": "needs you", "working": "working", "done": "done", "failed": "failed",
           "stopped": "stopped", "stale": "no news", "planned": "planned", "waiting": "waiting",
           "ready": "ready", "skipped": "skipped", "blocked": "blocked"}
WF_CLOSED = ("done", "failed", "stopped")
OWNER_TTY = ("is yours to run, in your own terminal: it is refused without one (an agent reports steps; "
             "starting, gates and templates stay with you)")


def _wf_args(sub, args, specs=None, common=WF_COMMON):
    """(positionals, {flag: value, True or [values]}, the command after `--` for wrap). `--` ends the flags."""
    lo, hi, values, switches, repeat = (specs or WORKFLOW_SPECS)[sub]
    values, switches = values + common[0], switches + common[1]
    pos, opts, rest, i = [], {}, [], 0
    while i < len(args):
        a = args[i]
        if a == "--":
            rest = args[i + 1:]
            break
        if a == "-" or not a.startswith("--"):
            pos.append(a)
        else:
            key, eq, value = a.partition("=")
            if key in switches and not eq:
                opts[key] = True
            elif key in values:
                if not eq:
                    i += 1
                    if i >= len(args):
                        raise Refused(2, f"{key} needs a value")
                    value = args[i]
                if key in repeat:
                    opts.setdefault(key, []).append(value)
                else:
                    opts[key] = value
            else:
                raise Refused(2, f"unknown option {a} (takes {', '.join(values + switches)})")
        i += 1
    if sub != "wrap" and rest:
        pos += rest
        rest = []
    if not lo <= len(pos) <= hi:
        raise Refused(2, f"`{sub}` takes {lo if lo == hi else f'{lo} to {hi}'} argument"
                         f"{'' if hi == 1 else 's'} ({_me()} wf --help)")
    return pos, opts, rest


def _page_chars(cfg):
    """The map's units from the page the render loop last wrote (cfg out), so a step's planet is known here as
    it is in the bridge; [] when there is no page yet (the bridge places the steps when it reads them)."""
    path = cfg.get("out") or os.path.join(HERE, "game.html")
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("const W = ") and line.rstrip().endswith(";"):
                    world = json.loads(line[len("const W = "):].rstrip().rstrip(";"))
                    return world.get("chars") or [] if isinstance(world, dict) else []
    except (OSError, ValueError, UnicodeError):
        pass
    return []


def _wf_store(opts, chars=True):
    """(the workflow store, its task store, the config) of the map this shell means (resolved as `thread` does)."""
    import config
    import tasks
    import workflows
    cfg = _thread_config(opts)
    roots = tuple(config.task_roots(cfg)) or (os.path.expanduser("~/work"),)
    tstore = tasks.store_for(cfg, roots)
    store = workflows.store_for(cfg, task_store=tstore, roots=roots, chars=_page_chars(cfg) if chars else ())
    return store, tstore, cfg


def _token_file(path):
    """{"id", "token"} from a --token-file (mode 600, else refused)."""
    path = os.path.abspath(os.path.expanduser(path))
    try:
        mode = os.stat(path).st_mode & 0o777
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        raise Refused(1, f"no token file at {path} (`wf new --token-file {path}` writes one)") from None
    except (OSError, ValueError) as exc:
        raise Refused(1, f"the token file {path} cannot be read: {exc}") from None
    if mode & 0o077:
        raise Refused(1, f"the token file {path} is readable by others (mode {mode:o}); chmod 600 it")
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not isinstance(raw.get("token"), str):
        raise Refused(1, f"the token file {path} holds no workflow id and token")
    return raw


def _wf_creds(opts, pos_id=None):
    """(workflow id, token): an explicit ID, else the token file's, else $CM_WORKFLOW; the token from the token
    file, else $CM_WORKFLOW_TOKEN."""
    wid = tok = None
    if opts.get("--token-file"):
        raw = _token_file(opts["--token-file"])
        wid, tok = raw["id"], raw["token"]
    else:
        wid, tok = os.environ.get("CM_WORKFLOW") or None, os.environ.get("CM_WORKFLOW_TOKEN") or None
    wid = pos_id or wid
    if not wid:
        raise Refused(2, "which workflow? give its ID, --token-file PATH, or set $CM_WORKFLOW")
    return wid, tok


def _owner_only(what):
    if not os.isatty(0):
        raise Refused(1, f"`{what}` {OWNER_TTY}")


def _wf_text(s, wid=None):
    """A workflow as text: its line, its goal, one row per step (mark, label, where, state, what it says)."""
    def width(rows, i):
        return min(32, max([len(r[i]) for r in rows] + [4]))
    by = (s.get("by") or {}).get("name") or "an agent"
    mins = int((s.get("elapsed_s") or 0) // 60)
    head = (f"{s.get('title')} · {WF_WORD.get(s.get('state'), s.get('state'))} · {s.get('done', 0)} of "
            f"{s.get('total', 0)} done · by {by}" + (f" · {mins} min" if mins else ""))
    lines = [head]
    if s.get("goal") and s.get("goal") != s.get("title"):
        lines.append("  " + s["goal"])
    rows = []
    for n, st in enumerate(s.get("steps") or [], 1):
        bits = [st.get("says") or ""]
        prog = st.get("progress")
        if isinstance(prog, dict):
            bits.insert(0, f"{prog.get('done')}/{prog.get('total')}")
        if st.get("output"):
            bits.append("made " + str(st["output"]))
        if st.get("why") and st.get("why") not in bits:
            bits.append(str(st["why"]))
        if st.get("kind") == "gate" and st.get("state") == "needs_you":
            bits.append(f"approve it on the map, or: {_me()} wf gate {s.get('id') or wid} {st.get('key')} --pass")
        rows.append([f"{WF_MARK.get(st.get('state'), '?')} {n}", str(st.get("label") or st.get("key")),
                     str(st.get("place") or ""), WF_WORD.get(st.get("state"), str(st.get("state"))),
                     " · ".join(b for b in bits if b)])
    if rows:
        w1, w2, w3 = width(rows, 1), width(rows, 2), width(rows, 3)
        for r in rows:
            lines.append(f"  {r[0]:<4} {r[1][:w1]:<{w1}}  {r[2][:w2]:<{w2}}  {r[3]:<{w3}}  {r[4]}".rstrip())
    if s.get("sentence"):
        lines.append("  " + s["sentence"])
    lines.append(f"  id {s.get('id') or wid}")
    return "\n".join(lines)


def _wf_wait(store, tstore, wid, stream):
    """Until the workflow is done, failed or stopped: its sentence on `stream` when it changes, the engine and
    the threads' oracle loop ticked every TICK_SECONDS (no bridge needed). Returns the last summary."""
    import workflows
    said, next_tick = None, 0.0
    try:
        while True:
            try:
                s = store.summary(wid)
            except workflows.WorkflowError as exc:
                raise Refused(1, exc.message) from None
            if s.get("sentence") != said:
                said = s.get("sentence")
                print("· " + str(said or WF_WORD.get(s["state"], s["state"])), file=stream, flush=True)
            if s["state"] in WF_CLOSED:
                return s
            if time.monotonic() >= next_tick:
                next_tick = time.monotonic() + TICK_SECONDS
                for tick in (tstore.tick, store.tick):
                    with contextlib.suppress(Exception):
                        tick()
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        raise Refused(130, f"stopped waiting; {wid} goes on: {_me()} wf show {wid}") from None


def _wf_open_token_file(path):
    """The token file, created empty and private before the workflow is made (an existing file is never
    overwritten: refused, untouched). Returns (fd, path)."""
    path = os.path.abspath(os.path.expanduser(path))
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        raise Refused(1, f"{path} already exists; nothing was overwritten (pick a new --token-file)") from None
    except OSError as exc:
        raise Refused(1, f"the token file {path} cannot be made: {exc.strerror or exc}") from None
    os.fchmod(fd, 0o600)
    return fd, path


def _wf_lost_token(store, made, path, exc):
    """The token file could not be written. Take away the file this call reserved (never anyone else's) and stop the
    workflow, whose token nobody holds now. Returns the Refused that says what happened to both."""
    with contextlib.suppress(OSError):
        os.unlink(path)
    try:
        store.stop(made["id"], "its token could not be saved", made["token"])
        fate = f"{made['id']} was made and stopped (its token was not kept: make it again)"
    except Exception as err:                    # said, not swallowed: the owner can still stop it
        fate = (f"{made['id']} was made and could not be stopped ({getattr(err, 'message', err)}): "
                f"{_me()} wf stop {made['id']}")
    return Refused(1, f"the token file {path} could not be written ({getattr(exc, 'strerror', None) or exc}); {fate}")


def _wf_made(store, tstore, made, opts, as_json, fd=None, path=None):
    """Say what `new` made: the id and the token (once), or the token into the token file."""
    if fd is not None:
        data = json.dumps({"id": made["id"], "token": made["token"]}).encode("utf-8")
        try:
            try:
                view = memoryview(data)
                while view:                      # a write may take only part of it
                    wrote = os.write(fd, view)
                    if wrote <= 0:
                        raise OSError(errno.EIO, "wrote nothing")
                    view = view[wrote:]
            finally:
                os.close(fd)
        except BaseException as exc:
            lost = _wf_lost_token(store, made, path, exc)
            if isinstance(exc, OSError):
                raise lost from None
            raise
    state = made["workflow"]["state"]
    payload = {"ok": True, "id": made["id"], "state": state, "start": made["start"], "notes": made.get("notes", [])}
    if path:
        payload["token_file"] = path
    else:
        payload["token"] = made["token"]
    lines = [f"id     {made['id']}"]
    lines.append(f"token  in {path} (mode 600)" if path else
                 f"token  {made['token']}   (shown once: keep it, or pass --token-file PATH next time)")
    if state == "planned":
        lines.append("state  planned: press Start on its card on the map (or `wf start ID` in your terminal)")
    lines += [f"note   {n}" for n in made.get("notes") or []]
    if not opts.get("--wait"):
        _say(as_json, payload, "\n".join(lines))
        return 0
    print("\n".join(lines) if not as_json else f"workflow {made['id']}", file=sys.stderr if as_json else sys.stdout,
          flush=True)
    s = _wf_wait(store, tstore, made["id"], sys.stderr if as_json else sys.stdout)
    _say(as_json, dict(payload, workflow=s, waited=True), _wf_text(s))
    return 0 if s["state"] == "done" else 1


def _wf_quick_spec(title, steps, by):
    keys, out, prev = set(), [], None
    for n, raw in enumerate(steps, 1):
        label, at, where = raw.rpartition("@") if "@" in raw else (raw, "", "")
        label, where = (label or raw).strip(), where.strip()
        key = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:20] or f"s{n}"
        while key in keys:
            key = f"{key[:20]}-{n}"
        keys.add(key)
        st = {"key": key, "label": label[:60], "kind": "note"}
        if where:
            st["at"] = where if where.startswith(("place:", "machine:")) else os.path.abspath(os.path.expanduser(where))
        if prev:
            st["after"] = [prev]
        out.append(st)
        prev = key
    return {"title": title, "by": {"name": by}, "steps": out}


def _wf_depth():
    raw = os.environ.get("CM_WORKFLOW_DEPTH", "0")
    return int(raw) if raw.isdigit() else 0


def _wf_new(store, tstore, cfg, pos, opts, as_json):
    # every way of making a workflow (a spec, the quick form, a template, Run again) is checked first and then made by
    # `make`; --token-file is reserved once, between the two, so none of them can print a token the file was asked for
    if opts.get("--from") or opts.get("--template"):
        if pos or opts.get("--step"):
            raise Refused(2, "--from and --template make a workflow of their own: no spec file and no --step with them")
        _owner_only("wf new --from" if opts.get("--from") else "wf new --template")
        if opts.get("--from"):
            def make():
                return store.run_again(opts["--from"])
        else:
            if not opts.get("--at") or not opts.get("--goal"):
                raise Refused(2, "--template needs --at PATH and --goal \"one sentence\"")
            runner = opts.get("--runner") or next((n for n, s in tstore.runners.items()
                                                   if s.get("role", "both") != "check"), None)
            if not runner:
                raise Refused(1, "no agent is set up to run it (" + _offered(tstore, "check") + ")")

            def make():
                return store.new_from_template(opts["--template"], os.path.abspath(os.path.expanduser(opts["--at"])),
                                               opts["--goal"], runner)
    else:
        by = opts.get("--by")
        if opts.get("--step"):
            if not pos:
                raise Refused(2, "the quick form is: wf new \"Title\" --step \"Label@/path\" [--step ...]")
            spec = _wf_quick_spec(pos[0], opts["--step"], by or os.environ.get("CM_AGENT_NAME") or "an agent")
        else:
            if not pos:
                raise Refused(2, f"usage: {_me()} wf new SPEC.json|- (or the quick form with --step; wf --help)")
            try:
                raw = sys.stdin.read() if pos[0] == "-" else open(os.path.expanduser(pos[0]), encoding="utf-8").read()
                spec = json.loads(raw)
            except OSError as exc:
                raise Refused(1, f"cannot read {pos[0]}: {exc.strerror or exc}") from None
            except ValueError as exc:
                raise Refused(1, f"the spec is not JSON: {exc}") from None
            if not isinstance(spec, dict):
                raise Refused(1, "a workflow spec is a JSON object {title, steps, ...} (`wf schema` shows it)")
        if opts.get("--plan"):
            spec["start"] = "plan"

        def make():
            return store.create(spec, by={"name": by} if by else None, depth=_wf_depth())
    fd = path = None
    if opts.get("--token-file"):
        fd, path = _wf_open_token_file(opts["--token-file"])
    try:
        made = make()
    except BaseException:
        if fd is not None:
            os.close(fd)
            with contextlib.suppress(OSError):
                os.unlink(path)             # the empty file this call made a moment ago, never anyone else's
        raise
    return _wf_made(store, tstore, made, opts, as_json, fd, path)


def _wf_step(store, pos, opts, as_json):
    wid, tok = _wf_creds(opts, pos[0] if len(pos) == 2 else None)
    key = pos[-1]
    if not any(opts.get(k) for k in ("--state", "--says", "--progress", "--output")):
        raise Refused(2, "say what changed: --state, --says, --progress or --output")
    out = opts.get("--output")
    if out and not out.startswith("/") and os.path.isfile(out):
        out = os.path.abspath(out)              # a file named from here is a file, not words
    got = store.step(wid, key, tok, opts.get("--state"), opts.get("--says"), opts.get("--progress"), out)
    s = got.get("workflow") or {}
    _say(as_json, got, "ok · " + (s.get("sentence") or "noted") if not got.get("coalesced") else "ok (folded into "
         "the last update: the map shows it)")
    return 0


def _wf_show(store, tstore, pos, opts, as_json):
    wid, _ = _wf_creds(opts, pos[0] if pos else None)
    if opts.get("--wait"):
        s = _wf_wait(store, tstore, wid, sys.stderr)
        _say(as_json, {"ok": True, "workflow": s, "waited": True}, _wf_text(s))
        return 0 if s["state"] == "done" else 1
    s = store.summary(wid)
    _say(as_json, {"ok": True, "workflow": s}, _wf_text(s))
    return 0


def _wf_list(store, as_json):
    rows = store.list()
    if as_json:
        _say(True, {"ok": True, "workflows": rows}, "")
        return 0
    if not rows:
        print("no workflows yet (`wf new`, or `wf demo` for a look)")
    for s in rows:
        print(f"{WF_WORD.get(s['state'], s['state']):<10} {s['done']:>2} of {s['total']:<2}  {s['title']}  ({s['id']})")
    return 0


WRAP_GRACE = (10.0, 3.0, 3.0)        # seconds `wf wrap` waits after the interrupt, after TERM, after KILL


class _WrapStop(BaseException):
    """INT, TERM or HUP reaching `wf wrap`: ends its wait for the command."""

    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


class _WrapSignals:
    """What `wf wrap` does with INT, TERM and HUP. The first one raises _WrapStop where the wrapper waits, which ends
    the wait. Every later one is only counted, never raised: it cannot cut the cleanup short (the wrapper once gave
    its handlers back before it stopped the command, so a second signal killed the wrapper and left the command
    running); the cleanup reads the count and takes each as "hurry": on to the next, harder signal."""

    def __init__(self):
        self.count = 0
        self.live = True                  # the next signal may still raise

    def handle(self, signum, _frame):
        self.count += 1
        if self.live:
            self.live = False
            raise _WrapStop(signum)


def _wf_group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wf_stop_group(proc, first, signals=None):
    """Stop what a wrap started, all of it: its process group gets `first` (the signal that interrupted the wrap),
    then TERM, then KILL, each after its grace (WRAP_GRACE), until no member is left; the command is reaped before
    this returns. Each further signal the wrapper gets meanwhile (`signals`, see _WrapSignals) skips to the next,
    harder step. A stopped command is continued after each signal, or it would never act on it."""
    pgid = proc.pid                   # its own process group: the group id is its pid
    seen = 1                          # the signal that began this
    for sig, grace in zip((first, signal.SIGTERM, signal.SIGKILL), WRAP_GRACE):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, sig)
            if sig != signal.SIGKILL:
                os.killpg(pgid, signal.SIGCONT)
        end = time.monotonic() + grace
        while time.monotonic() < end:
            proc.poll()               # reaps the leader the moment it is gone: a zombie leader keeps the group "alive"
            if not _wf_group_alive(pgid):
                return
            if signals is not None and signals.count > seen:
                seen += 1
                break
            time.sleep(0.05)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(5)


# ---- `wf wrap` on a terminal: the command in its own process group, in the wrapper's session, as the foreground job
def _wf_tty_mode():
    """None unless stdin is the terminal this process runs under; then True when the wrapper is that terminal's
    foreground group (it may hand the terminal to the command), False when it is a background job."""
    try:
        if threading.current_thread() is not threading.main_thread() or not os.isatty(0):
            return None
        return os.tcgetpgrp(0) == os.getpgrp()
    except OSError:                       # a terminal, but not this process's own: nothing to keep
        return None


def _wf_tty_give(pgid):
    """Make `pgid` the foreground group of the terminal on stdin (SIGTTOU ignored for it: a background group that
    changes the foreground group is stopped otherwise)."""
    old = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    try:
        os.tcsetpgrp(0, pgid)
    except OSError:
        pass
    finally:
        signal.signal(signal.SIGTTOU, old if old is not None else signal.SIG_DFL)


def _wf_tty_back(proc):
    """The wrapper takes the terminal back from the command's group (or from the one a command that did not start
    was in), and only from that: a terminal the shell has taken is left to it."""
    try:
        holder = os.tcgetpgrp(0)
    except OSError:
        return
    if holder != os.getpgrp() and (proc is None or holder == proc.pid):
        _wf_tty_give(os.getpgrp())


def _wf_child_group(foreground):
    """Runs in the new child before exec: a process group of its own (in the wrapper's session, so /dev/tty still
    works for it), made the terminal's foreground group when the wrapper held it. Never stops the command starting."""
    os.setpgid(0, 0)
    if foreground:
        _wf_tty_give(os.getpgrp())


def _wf_stopped(proc):
    """The command's group was stopped (Ctrl-Z, or a read of the terminal from the background). Do what a shell does
    with its job: take the terminal back, stop with it so whatever started the wrapper sees one stopped job, and
    when that is continued, hand the terminal (if it came to the wrapper) and SIGCONT back to the command."""
    _wf_tty_back(proc)
    os.kill(os.getpid(), signal.SIGSTOP)
    with contextlib.suppress(OSError):
        if os.tcgetpgrp(0) == os.getpgrp():
            _wf_tty_give(proc.pid)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGCONT)


def _wf_wait_command(proc, tty):
    """proc.wait(); on a terminal, a command that is stopped is noticed too (see _wf_stopped)."""
    if tty is None:
        return proc.wait()
    while True:
        _, status = os.waitpid(proc.pid, os.WUNTRACED)
        if not os.WIFSTOPPED(status):
            proc.returncode = os.waitstatus_to_exitcode(status)
            return proc.returncode
        _wf_stopped(proc)


def _wf_wrap(store, opts, cmd):
    import workflows
    if not cmd:
        raise Refused(2, f"usage: {_me()} wf wrap [--title T] [--at PATH] -- CMD...")
    title = (opts.get("--title") or " ".join(cmd))[:60]
    at = opts.get("--at")
    if at:
        at = os.path.abspath(os.path.expanduser(at))
    elif any(os.path.realpath(os.getcwd()) == r or os.path.realpath(os.getcwd()).startswith(r.rstrip("/") + "/")
             for r in store._real_roots()):
        at = os.getcwd()
    by = opts.get("--by") or os.environ.get("CM_AGENT_NAME") or "wrap"
    spec = {"title": title, "by": {"name": by}, "steps": [{"key": "run", "label": title, "kind": "note",
                                                           **({"at": at} if at else {})}]}
    wid = tok = None
    depth = _wf_depth()
    try:
        made = store.create(spec, depth=depth)
        wid, tok = made["id"], made["token"]
        store.step(wid, "run", tok, "working", says=("running " + os.path.basename(cmd[0]))[:200])
    except (workflows.WorkflowError, OSError) as exc:      # the command runs anyway: a wrap never stops it
        print(f"container-map wf wrap: not on the map ({getattr(exc, 'message', exc)}); running it anyway",
              file=sys.stderr, flush=True)
    env = dict(os.environ)
    if wid:
        env.update(CM_WORKFLOW=wid, CM_WORKFLOW_TOKEN=tok, CM_WORKFLOW_DEPTH=str(depth + 1))
    t0 = time.monotonic()
    proc = stopped = missing = None
    rc = 0
    signals, handlers = _WrapSignals(), {}
    tty = _wf_tty_mode()
    try:
        try:
            # like Ctrl-C, these end the wait and stop the command's group; they stay in force until that is done
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                if sig == signal.SIGINT and signal.getsignal(sig) == signal.SIG_IGN:
                    continue                       # started with INT ignored (a background job): keep it that way
                with contextlib.suppress(ValueError, OSError):         # only the main thread can set a handler
                    handlers[sig] = signal.signal(sig, signals.handle)
            if tty is None:
                # no terminal on stdin: a session of its own, so the command leads a process group nothing else is
                # in, and what it starts can be found and stopped with it
                proc = subprocess.Popen(cmd, env=env, start_new_session=True)
            else:
                # on a terminal it stays in the wrapper's session (the controlling terminal, so a prompt through
                # /dev/tty works), in a process group of its own, and is the foreground job while it runs
                proc = subprocess.Popen(cmd, env=env, preexec_fn=functools.partial(_wf_child_group, tty))
            rc = _wf_wait_command(proc, tty)
        except FileNotFoundError:
            rc, missing = 127, f"{cmd[0]}: command not found"
        except _WrapStop as stop:
            rc, stopped = 128 + stop.signum, stop.signum
        finally:
            signals.live = False               # whatever comes now is counted, never raised
        if tty:
            _wf_tty_back(proc)                 # before anything is printed or signalled
        if missing:
            print(f"container-map wf wrap: {missing}", file=sys.stderr)
        if stopped is not None and proc is not None:
            _wf_stop_group(proc, stopped, signals)
    finally:
        for sig, old in handlers.items():
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, old)
    if rc < 0:
        rc = 128 - rc                          # killed by a signal: 128 plus its number, as a shell says it
    secs = time.monotonic() - t0
    if wid:
        took = f"{secs:.1f} s" if secs < 120 else f"{secs / 60:.0f} min"
        says = ("interrupted" if stopped is not None else "finished" if rc == 0 else f"exit {rc}") + f" after {took}"
        with contextlib.suppress(workflows.WorkflowError, OSError):
            store.step(wid, "run", tok, "done" if rc == 0 else "failed", says=says)
    return rc


def _wf_demo(store, cfg, opts, as_json):
    """A 4-step note workflow on this map, part way through: one step done, one at work (120 of 800), two
    waiting. The planets: api, ops and docs when the map has them, else its first three."""
    planets = [(c.get("name"), c.get("path")) for c in store._chars or []
               if isinstance(c, dict) and c.get("kind") == "area" and isinstance(c.get("path"), str)
               and not c.get("alias_of")]
    if not planets:
        import districts
        planets = [(n, p) for n, p, link in districts.planet_roots(cfg)[0] if not link]
    if not planets:
        raise Refused(1, "this map has no planets yet (folders under its root)")
    by_name = dict(planets)
    pick = [by_name.get(n) for n in ("api", "ops", "docs")]
    rest = iter(p for _, p in planets if p not in pick)
    pick = [p or next(rest, planets[0][1]) for p in pick]
    spec = {"title": "Fix the phone labels", "goal": "Every planet name on the phone readable at 13 px, none cut",
            "by": {"name": opts.get("--by") or "Claude Code"}, "steps": [
                {"key": "find", "label": "Find the cut labels", "at": pick[0], "kind": "note"},
                {"key": "fix", "label": "Fix the label slots", "at": pick[1], "kind": "note", "after": ["find"]},
                {"key": "test", "label": "Run the tests", "at": pick[1], "kind": "note", "after": ["fix"]},
                {"key": "docs", "label": "Check the docs page", "at": "place:docs site", "kind": "note",
                 "after": ["test"]}]}
    made = store.create(spec)
    wid, tok = made["id"], made["token"]
    store.step(wid, "find", tok, "working", says="looking at every planet name on a 390 px screen")
    store.step(wid, "find", tok, "done", says="6 labels are cut on the phone")
    store.step(wid, "fix", tok, "working", says="moving the name slots", progress="120/800")
    s = store.summary(wid)
    _say(as_json, {"ok": True, "id": wid, "token": tok, "workflow": s},
         _wf_text(s) + f"\n  token {tok}   (drive it: {_me()} wf step {wid} fix --state done)")
    return 0


def _wf_retire(store, cfg, opts, as_json):
    raw = (opts.get("--older") or "").strip().lower()
    if not re.fullmatch(r"\d{1,5}d?", raw) or int(raw.rstrip("d")) < 1:
        raise Refused(2, "--older takes days, e.g. --older 30d")
    attic = opts.get("--attic") or os.path.join(cfg.get("work_root") or os.path.dirname(store.workflows_dir),
                                                "_attic", "workflows-" + time.strftime("%Y-%m-%d"))
    got = store.retire(int(raw.rstrip("d")), os.path.abspath(os.path.expanduser(attic)))
    n = len(got["moved"])
    _say(as_json, dict(got, ok=True), f"moved {n} closed workflow{'' if n == 1 else 's'} to {got['attic']}"
         + (" (MANIFEST.tsv and UNDO.sh there)" if n else ""))
    return 0


def _wf_templates(store, as_json):
    rows = store.list_templates()
    if as_json:
        _say(True, {"ok": True, "templates": rows}, "")
        return 0
    for t in rows:
        print(f"{t['name']:<16} {t['label']} · {t['steps']} steps · {t['desc']}")
    return 0


def cmd_workflow(args):
    head = args[:args.index("--")] if "--" in args else args
    as_json = "--json" in head
    if not args or args[0] in ("-h", "--help", "help"):
        print(WORKFLOW_USAGE)
        return 0
    sub = args[0] if args[0] in WORKFLOW_SPECS else ""
    if sub == "pulse":
        return _wf_pulse(args[1:])
    if sys.version_info[:2] < MIN_PYTHON:
        return _wf_failed(as_json, 1, f"workflows need python {_floor()}+", sub)
    import tasks
    import workflows
    try:
        if not sub:
            raise Refused(2, f"unknown workflow command {args[0]!r} ({', '.join(WORKFLOW_SPECS)}; {_me()} wf --help)")
        pos, opts, rest = _wf_args(sub, args[1:])
        if sub == "schema":
            print(json.dumps(workflows.schema(), indent=None if as_json else 1))
            return 0
        if sub in ("start", "gate", "save"):
            _owner_only(f"wf {sub}")
        store, tstore, cfg = _wf_store(opts, chars=sub not in ("list", "templates"))
        if sub == "new":
            return _wf_new(store, tstore, cfg, pos, opts, as_json)
        if sub == "step":
            return _wf_step(store, pos, opts, as_json)
        if sub == "show":
            return _wf_show(store, tstore, pos, opts, as_json)
        if sub == "list":
            return _wf_list(store, as_json)
        if sub == "done":
            wid, tok = _wf_creds(opts, pos[0] if pos else None)
            s = store.done(wid, tok)
            _say(as_json, {"ok": True, "workflow": s}, "ok · " + (s.get("sentence") or WF_WORD[s["state"]]))
            return 0
        if sub == "stop":
            wid, tok = _wf_creds(opts, pos[0] if pos else None)
            if tok:
                got = store.stop(wid, opts.get("--why"), tok)
            else:
                _owner_only("wf stop ID (with no token)")
                got = store.stop(wid, opts.get("--why"), owner=True, tasks=bool(opts.get("--tasks")))
            for e in got.get("errors") or []:
                print(f"container-map wf stop: {e}", file=sys.stderr)
            _say(as_json, got, f"stopped {wid}" + (f"; its tasks stopped: {', '.join(got['stopped_tasks'])}"
                                                   if got.get("stopped_tasks") else ""))
            return 0
        if sub == "start":
            s = store.start(pos[0])
            lines = store.shell_lines(pos[0])
            _say(as_json, {"ok": True, "workflow": s, "shell_lines": lines},
                 f"started {pos[0]}" + "".join(f"\n  runs as you ({ln['label']}): "
                                                + "; ".join(x for x in (ln.get('setup'), ln.get('verify')) if x)
                                                for ln in lines))
            return 0
        if sub == "gate":
            if bool(opts.get("--pass")) == bool(opts.get("--fail")):
                raise Refused(2, "say --pass (approve) or --fail (reject)")
            s = store.gate(pos[0], pos[1], bool(opts.get("--pass")), opts.get("--why"))
            _say(as_json, {"ok": True, "workflow": s}, ("approved" if opts.get("--pass") else "rejected")
                 + f" · {s.get('sentence') or WF_WORD.get(s['state'], s['state'])}")
            return 0
        if sub == "wrap":
            return _wf_wrap(store, opts, rest)
        if sub == "demo":
            return _wf_demo(store, cfg, opts, as_json)
        if sub == "retire":
            return _wf_retire(store, cfg, opts, as_json)
        if sub == "templates":
            return _wf_templates(store, as_json)
        if sub == "save":
            if not opts.get("--as"):
                raise Refused(2, "name it: wf save ID --as NAME")
            got = store.save_template(pos[0], opts["--as"])
            _say(as_json, got, f"kept as template {got['name']} (`wf new --template {got['name']} --at PATH "
                               "--goal ...`)")
            return 0
        raise Refused(2, f"unknown workflow command {sub!r}")
    except Refused as exc:
        return _wf_failed(as_json, exc.code, exc.message, sub)
    except workflows.WorkflowError as exc:
        return _wf_failed(as_json, 1, exc.message, sub)
    except tasks.TaskError as exc:
        return _wf_failed(as_json, 1, exc.message, sub)
    except (OSError, ValueError) as exc:
        return _wf_failed(as_json, 1, f"workflow store: {exc}", sub)


def _wf_failed(as_json, code, message, sub=""):
    if as_json:
        print(json.dumps({"ok": False, "error": message, "exit": code}), flush=True)
    else:
        print(f"container-map wf{' ' + sub if sub else ''}: {message}", file=sys.stderr, flush=True)
    return code


def _wf_pulse(args):
    """`wf pulse [ID] [KEY]`: still alive, written to latest.json only. Always exit 0 and silent: it rides on an
    agent's hook (after every tool call), and a hook must never fail the session it rides on."""
    try:
        import workflows
        pos, opts, _ = _wf_args("pulse", args)
        if not opts.get("--token-file") and not os.environ.get("CM_WORKFLOW") and not (
                pos and workflows.ID_RE.fullmatch(pos[0])):
            return 0
        if opts.get("--token-file") and not os.path.exists(os.path.expanduser(opts["--token-file"])):
            return 0
        explicit = pos[0] if pos and workflows.ID_RE.fullmatch(pos[0]) else None
        wid, tok = _wf_creds(opts, explicit)
        key = pos[-1] if pos and pos[-1] != explicit else None
        store, _, _ = _wf_store(opts, chars=False)
        store.pulse(wid, tok, key)
    except BaseException:                   # silent by design; Ctrl-C included
        pass
    return 0


# ---- `container-map show`: paint on the running map for a while ---------------------------------
SHOW_USAGE = """container-map show: paint on the running map for a while (an agent's way to show anything)

  show PATH|UID|place:NAME "caption" [--seconds N]     point at it: an arrow, the caption in the status bar
  show --focus PATH|UID          centre it        show --follow ID     select a workflow and keep it in view
  show card --at PATH|UID|place:NAME --title T [--row K=V ...] [--text T] [--bar 3/9]
  show chart --at PATH|UID|place:NAME --label T --values 1,4,9 [--unit ms] [--kind spark|bars]
  show page --title T --html-file FILE|- [--at PATH|UID|place:NAME]
                                          your own HTML page, which the person opens in a sandboxed panel on the
                                          map (all JS and CSS inline; no network; <= 256 KiB; no --at: place:pages)
  show put SPEC.json|- [--ttl 300]        any show: {ops: [...], at, by, ttl, id} (`wf schema` lists the ops)
  show clear ID                           take one down before its time

  --by NAME (default $CM_AGENT_NAME, else "an agent": the map names the author on it) goes with all but clear.
  card, chart, page and put make a stored show and take --ttl SECONDS (30-900, default 300; a show with a page:
  up to 86400, default 1800) and --id ID (the same id replaces it); card and chart also take --xray (3D: drawn
  through planets; in a put spec: "xray": true on an op).
  The point, --focus and --follow are control actions, not stored shows: they take --by (and --seconds for the point)
  and ignore --ttl, --id and --xray. focus and follow obey your setting "Agents can move my view".
  Shows never start or change work.
  The running map is found like gamectl finds it: --live-file, $BRIDGE_LIVE_PATH, else --root/--port.
  Exit: 0 shown; 1 refused or no map answers (the reason on stderr); 2 usage.
"""
SHOW_COMMON = (("--by", "--ttl", "--id", "--port", "--root", "--live-file", "--seconds", "--focus", "--follow"),
               ("--json", "--xray"))
SHOW_SPECS = {
    "point": (0, 2, (), (), ()),
    "card": (0, 0, ("--at", "--title", "--row", "--text", "--bar"), (), ("--row",)),
    "chart": (0, 0, ("--at", "--label", "--values", "--unit", "--kind"), (), ()),
    "page": (0, 0, ("--at", "--title", "--html-file"), (), ()),
    "put": (1, 1, (), (), ()),
    "clear": (1, 1, (), (), ()),
}


def _live_info(opts):
    """(path, JSON) of the live file of the running map this shell means (--live-file, $BRIDGE_LIVE_PATH, else --root
    and --port as gamectl finds it): its port and agent token. Refused when there is none, or it is not private."""
    import config
    port = opts.get("--port") or ""
    if port and not (port.isdigit() and 1 <= int(port) <= 65535):
        raise Refused(2, f"port must be a number from 1 to 65535, got {port!r}")
    try:
        live = opts.get("--live-file") or config.resolve_live(opts.get("--root") or "", int(port or 0))
    except RuntimeError as exc:
        raise Refused(1, str(exc)) from None
    try:
        mode = os.stat(live).st_mode & 0o777
        with open(live, encoding="utf-8") as handle:
            info = json.load(handle)
    except (OSError, ValueError):
        raise Refused(1, f"no running map found (looked for {live}); start it with `{_me()} serve`") from None
    if mode & 0o077 or not isinstance(info, dict) or not isinstance(info.get("port"), int) or not info.get(
            "agent_token"):
        raise Refused(1, f"{live} is not a private live file of a running map")
    return live, info


def _bridge_call(opts, method, path, body=None):
    """(status, JSON) of one call to the running map with its agent token (read from the mode-600 live file)."""
    live, info = _live_info(opts)
    headers = {"Authorization": "Bearer " + str(info["agent_token"])}
    payload = None
    if body is not None:
        payload = json.dumps(body)
        headers["Content-Type"] = "application/json"
    try:
        conn = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=15)
        conn.request(method, path, body=payload, headers=headers)
        reply = conn.getresponse()
        data = reply.read()
        conn.close()
    except (OSError, http.client.HTTPException) as exc:
        raise Refused(1, f"no map answers on 127.0.0.1:{info['port']} ({getattr(exc, 'strerror', None) or exc})") from None
    try:
        value = json.loads(data.decode("utf-8"))
    except ValueError:
        value = {"error": data[:200].decode("utf-8", "replace")}
    return reply.status, value


def _show_at(value):
    """An anchor as the bridge takes it: a uid or place:NAME as given, a folder path made absolute."""
    if not value:
        return value
    if ":" in value and not value.startswith(("/", ".", "~")):
        return value
    return os.path.abspath(os.path.expanduser(value))


def _show_reply(status, got, as_json, ok_text):
    if status not in (200, 202):
        raise Refused(1, str(got.get("error") or f"the map answered {status}"))
    for why in got.get("errors") or []:
        print(f"container-map show: skipped {why}", file=sys.stderr)
    if got.get("clamped"):
        print(f"container-map show: cut to fit: {', '.join(got['clamped'])}", file=sys.stderr)
    _say(as_json, got, ok_text(got))
    return 0


def cmd_show(args):
    head = args[:args.index("--")] if "--" in args else args
    as_json = "--json" in head
    if not args or args[0] in ("-h", "--help", "help"):
        print(SHOW_USAGE)
        return 0
    sub = args[0] if args[0] in ("card", "chart", "page", "put", "clear") else "point"
    try:
        pos, opts, _ = _wf_args(sub, args[1:] if sub != "point" else args, SHOW_SPECS, SHOW_COMMON)
        by = opts.get("--by") or os.environ.get("CM_AGENT_NAME") or None
        show = {}
        if by:
            show["by"] = by
        for flag, key in (("--ttl", "ttl"), ("--id", "id")):
            if opts.get(flag):
                show[key] = int(opts[flag]) if key == "ttl" and opts[flag].isdigit() else opts[flag]
        xray = {"xray": True} if opts.get("--xray") else {}
        if sub == "point":
            stamp = "cli-" + secrets.token_hex(6)
            if opts.get("--focus") or opts.get("--follow"):
                if pos or (opts.get("--focus") and opts.get("--follow")):
                    raise Refused(2, "--focus X or --follow ID, alone")
                action = ({"type": "focus", "target": _show_at(opts["--focus"])} if opts.get("--focus")
                          else {"type": "follow", "workflow": opts["--follow"]})
            else:
                if not pos:
                    raise Refused(2, "usage: show PATH|UID|place:NAME \"caption\" [--seconds N] (show --help)")
                action = {"type": "point", "target": _show_at(pos[0])}
                if len(pos) > 1:
                    action["text"] = pos[1]
                if opts.get("--seconds"):
                    if not opts["--seconds"].isdigit():
                        raise Refused(2, "--seconds takes a whole number (5-120)")
                    action["seconds"] = int(opts["--seconds"])
            if by:                         # who is pointing: the page names them wherever it draws this
                action["by"] = by
            status, got = _bridge_call(opts, "POST", "/control", {"request_id": stamp, "action": action})
            return _show_reply(status, got, as_json, lambda g: f"sent ({action['type']}); the map shows it when "
                                                                "its owner lets agents move the view")
        if sub == "clear":
            status, got = _bridge_call(opts, "POST", "/agent/show/clear", {"id": pos[0]})
            return _show_reply(status, got, as_json, lambda g: f"cleared {pos[0]}")
        if sub == "put":
            try:
                raw = json.loads(sys.stdin.read() if pos[0] == "-" else open(os.path.expanduser(pos[0]),
                                                                              encoding="utf-8").read())
            except OSError as exc:
                raise Refused(1, f"cannot read {pos[0]}: {exc.strerror or exc}") from None
            except ValueError as exc:
                raise Refused(1, f"the show is not JSON: {exc}") from None
            if isinstance(raw, list):
                raw = {"ops": raw}
            if not isinstance(raw, dict):
                raise Refused(1, "a show is {ops: [...], at, by, ttl, id} or a list of ops")
            for k, v in show.items():
                raw.setdefault(k, v)
            show = raw
        elif sub == "card":
            if not opts.get("--at") or not opts.get("--title"):
                raise Refused(2, "show card needs --at PATH|UID|place:NAME and --title T")
            op = {"op": "card", "title": opts["--title"], **xray}
            rows = []
            for r in opts.get("--row") or []:
                k, eq, v = r.partition("=")
                if not eq or not k:
                    raise Refused(2, f"--row takes KEY=VALUE, got {r!r}")
                rows.append([k, v])
            if rows:
                op["rows"] = rows
            if opts.get("--text"):
                op["text"] = opts["--text"]
            if opts.get("--bar"):
                if not re.fullmatch(r"\d{1,9}/\d{1,9}", opts["--bar"]):
                    raise Refused(2, "--bar takes A/B, e.g. 3/9")
                op["bar"] = [int(x) for x in opts["--bar"].split("/")]
            show.update(at=_show_at(opts["--at"]), ops=[op])
        elif sub == "chart":
            if not opts.get("--at") or not opts.get("--label") or not opts.get("--values"):
                raise Refused(2, "show chart needs --at PATH|UID|place:NAME, --label T and --values 1,4,9")
            try:
                values = [float(v) if any(c in v for c in ".eE") else int(v)
                          for v in opts["--values"].split(",") if v.strip()]
            except ValueError:
                raise Refused(2, "--values takes numbers separated by commas, e.g. 9,4,0") from None
            op = {"op": "chart", "label": opts["--label"], "values": values, **xray}
            if opts.get("--unit"):
                op["unit"] = opts["--unit"]
            if opts.get("--kind"):
                op["kind"] = opts["--kind"]
            show.update(at=_show_at(opts["--at"]), ops=[op])
        elif sub == "page":
            if not opts.get("--title") or not opts.get("--html-file"):
                raise Refused(2, "show page needs --title T and --html-file FILE (- reads stdin)")
            where = opts["--html-file"]
            try:
                if where == "-":
                    html = sys.stdin.read()
                else:
                    with open(os.path.expanduser(where), encoding="utf-8", newline="") as handle:
                        html = handle.read()
            except OSError as exc:
                raise Refused(1, f"cannot read {where}: {exc.strerror or exc}") from None
            except ValueError as exc:
                raise Refused(1, f"{where} is not UTF-8 text: {exc}") from None
            import workflows
            if len(html.encode("utf-8", "replace")) > workflows.BOUNDS["page_bytes"]:
                raise Refused(1, f"the page is over {workflows.BOUNDS['page_bytes']} bytes of UTF-8: "
                                 "make it smaller (inline only what it needs)")
            show["ops"] = [{"op": "page", "title": opts["--title"], "html": html}]
            if opts.get("--at"):
                show["at"] = _show_at(opts["--at"])
        status, got = _bridge_call(opts, "POST", "/agent/show", show)
        if sub == "page" and status == 200 and not got.get("id"):      # the whole show is the page: skipped is refused
            raise Refused(1, "no page was shown: " + ("; ".join(got.get("errors") or []) or "it was skipped"))
        return _show_reply(status, got, as_json, lambda g: (f"shown as {g['id']} until {g.get('expires')}"
                                                            if g.get("id") else "nothing to show: every op was skipped"))
    except Refused as exc:
        if as_json:
            print(json.dumps({"ok": False, "error": exc.message, "exit": exc.code}), flush=True)
        else:
            print(f"container-map show: {exc.message}", file=sys.stderr, flush=True)
        return exc.code


# ---- `container-map area set`: what a planet holds, by the owner ---------------------------------
AREA_USAGE = """usage: container-map area set PATH|area:NAME --kind K [--glyph G] [--config FILE] [--root DIR]

Say what a planet (a top-level folder) holds, so the map draws it as that: --kind is one of code, knowledge
(notes), reference, data, media, config, shared, archive, unknown (not sorted); --glyph one of code, notes,
money, health, career, journal, learning, archive, sync, tools, home, star (none: its purpose's own). It is
written to the areas.json the map reads (the bytes it replaces kept as areas.json.prev; the .prev before moves to
.prev.1, .prev.2 ..., never written over) and shows at the next render. A file or folder that is a link is left alone.
Yours: refused without a terminal. The page's planet card does the same (Change)."""
AREA_SPECS = {"set": (1, 1, ("--kind", "--glyph"), (), ())}


def cmd_area(args):
    if not args or args[0] in ("-h", "--help", "help"):
        print(AREA_USAGE)
        return 0
    try:
        if args[0] != "set":
            raise Refused(2, f"unknown area command {args[0]!r} (only: area set PATH --kind K [--glyph G])")
        pos, opts, _ = _wf_args("set", args[1:], AREA_SPECS, (("--config", "--root"), ("--json",)))
        if not opts.get("--kind"):
            raise Refused(2, "say what it holds: --kind K (area --help lists them)")
        _owner_only("container-map area set")
        import districts
        cfg = _thread_config(opts)
        roots = [(n, p) for n, p, _link in districts.planet_roots(cfg)[0]]
        want = pos[0]
        if want.startswith("area:"):
            name = want[5:] if any(n == want[5:] for n, _ in roots) else None
        else:
            real = os.path.realpath(os.path.expanduser(want))
            name = next((n for n, p in roots if os.path.realpath(p) == real), None)
            if name is None:
                inside = next((n for n, p in roots if real.startswith(os.path.realpath(p).rstrip("/") + "/")), None)
                if inside:
                    raise Refused(1, f"only a planet (a top-level folder) has a purpose; this folder is in "
                                     f"{inside}: area set area:{inside} ...")
        if not name:
            raise Refused(1, f"{want} is not a planet on this map ({', '.join(n for n, _ in roots[:12])}"
                             f"{' ...' if len(roots) > 12 else ''})")
        root = cfg.get("work_root") or ""
        path = cfg.get("areas_json") or (os.path.join(root, ".container-map", "areas.json") if root
                                         else os.path.join(HERE, "areas.json"))
        import bridge
        try:
            got = bridge.area_set_file(path, name, opts["--kind"], opts.get("--glyph"))
        except (ValueError, bridge.AreasBusy) as exc:
            raise Refused(1, str(exc)) from None
        except OSError as exc:
            raise Refused(1, f"could not write {path}: {exc.strerror or exc}") from None
        _say("--json" in args, dict(got, ok=True, name=name),
             f"{name} is now {got['kind']}" + (f" with the {got['glyph']} emblem" if got["glyph"] else "")
             + f" ({path}; the map shows it at its next render)")
        return 0
    except Refused as exc:
        print(f"container-map area: {exc.message}", file=sys.stderr, flush=True)
        return exc.code


# ---- `container-map mcp`: the agent API as MCP tools (container_map_mcp.py) ---------------------------------------
def cmd_mcp(args):
    import container_map_mcp       # not `mcp`: that name is the MCP SDK's package
    return container_map_mcp.main(args)


COMMANDS = {"render": cmd_render, "serve": cmd_serve, "link": cmd_link, "demo": cmd_demo, "config": cmd_config,
            "doctor": cmd_doctor, "init": cmd_init, "install": cmd_install,
            "uninstall": cmd_uninstall, "thread": cmd_thread, "pair": cmd_pair,
            "devices": cmd_devices, "workflow": cmd_workflow, "wf": cmd_workflow, "show": cmd_show,
            "area": cmd_area, "mcp": cmd_mcp}

HELP = {
    "render": """usage: container-map render [ROOT] [--out FILE] [--port N] [--config FILE]

Write the page once: a static file you can open with file://. ROOT is the folder to map (else
the config's work_root). --out says where the page goes. --port N is where its link to the live
map points (default 7428, or $BRIDGE_PORT). It never starts a server.""",
    "serve": """usage: container-map serve [ROOT] [--port N] [--lock] [--config FILE]

Run the bridge and a renderer that redraws the page every 15 seconds, and print the live URL.
Ctrl-C stops both. --port N is the loopback port (default 7428, or $BRIDGE_PORT). --lock (or "ui_lock":
true in the config) is for a computer other people log in to: the page then opens only through a private link
printed here (new at every start), so another user cannot open your map with a plain request.""",
    "link": """usage: container-map link [ROOT] [--port N]

Print the private link of the locked map that is running (started with `serve --lock`, `install --lock`
or "ui_lock": true). Only the user who started it can read it. The map is found by its port (--port N,
else $BRIDGE_PORT, else 7428) and the root above the current folder.""",
    "demo": """usage: container-map demo [--no-serve] [--port N] [--static] [--root DIR]

Build a demo workspace in a new folder, put two threads through the real engine with a scripted
agent (no agent CLI, no login), then serve the map. --no-serve stops after building, --port N
picks the port, --root DIR builds in an empty folder of yours, --static writes the older
one-page demo for file:// instead.""",
    "config": """usage: container-map config [ROOT] [--config FILE]

Print the resolved config as JSON: what will be read for ROOT, with the defaults filled in.""",
    "doctor": """usage: container-map doctor [ROOT] [--port N] [--config FILE]

Check this machine: python, git, bash, agent CLIs, the config (and the keys of it that are
ignored), the root, the port. Exit 0 when ready, 1 when not.""",
    "init": """usage: container-map init ROOT [--create] [--config FILE]

Write ROOT/.container-map/container.json from what ROOT holds (never overwrites one: it writes
container.json.new beside it). --create makes ROOT when it is not there.""",
    "install": """usage: container-map install ROOT [--port N] [--dry-run] [--lock] [--config FILE]

Run `serve ROOT` as a user service (systemd or launchd) and wait for its bridge to answer.
--dry-run prints the unit file and the commands and changes nothing. --lock runs `serve --lock` (see
`serve --help`); the private link is printed here, not into the service's log.""",
    "uninstall": """usage: container-map uninstall [--dry-run] [--force]

Stop that service. Its unit file is renamed, never deleted. --force retires the file even when
stopping failed (an install that failed before the service loaded). It reads no config file:
the service is found by its name.""",
    "pair": """usage: container-map pair [--hours N] [--port N] [--json]

Ask the running map for a one-use code to pair a phone, and print the address to open. A code
lives ten minutes; --hours N (1 to 168) makes one that waits up to a week, and survives a restart. Needs a `remote` block in the config the map was started with. It reads no config file
itself: the map is found by its port (--port N, else $BRIDGE_PORT, else 7428).""",
    "workflow": "usage: container-map " + WORKFLOW_USAGE.split(": ", 1)[0][len("container-map "):]
                + "\n\n" + WORKFLOW_USAGE.split("\n", 2)[2].rstrip(),
    "wf": "usage: container-map wf (= container-map workflow)\n\n" + WORKFLOW_USAGE.split("\n", 2)[2].rstrip(),
    "show": "usage: container-map show ...\n\n" + SHOW_USAGE.split("\n", 2)[2].rstrip(),
    "area": AREA_USAGE,
    "mcp": """usage: container-map mcp [--root R] [--port N] [--live-file F]

Serve the running map's agent API as MCP tools on stdin and stdout (newline-delimited JSON-RPC 2.0), for any
agent that speaks MCP: it can then read the map, hand out and check threads, and draw on the map (shows, and
pages of its own HTML that you open in a sandbox). Add it once to your agent, for example
`claude mcp add container-map -- container-map mcp`; docs/MCP.md has the lines for Codex and the tools.
The map is found like `show` finds it: --live-file, $BRIDGE_LIVE_PATH, else --root and --port (default: the map
above the current folder). Everything it logs goes to stderr; stdout is the protocol. Merging, closing and
setting aside stay with you: no tool can do them. It writes no config of yours.""",
    "devices": """usage: container-map devices [--revoke ID] [--port N] [--json]

List the paired phones, or end one at once with --revoke ID. It reads no config file itself:
the map is found by its port (--port N, else $BRIDGE_PORT, else 7428).""",
}


def _asks_for_help(rest):
    """Whether -h or --help is among the options (what follows a bare -- is not one)."""
    head = rest[:rest.index("--")] if "--" in rest else rest
    return any(a in ("-h", "--help") for a in head)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if argv[0] in ("--version", "-V"):
        print(f"container-map {VERSION}")
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd not in COMMANDS:
        _die(f"container-map: unknown command {cmd!r}\n{__doc__}")
    if _asks_for_help(rest):           # every command: its own help, and nothing else happens
        print(THREAD_USAGE.rstrip() if cmd == "thread" else HELP[cmd])
        return 0
    if cmd in ("workflow", "wf") and rest[:1] == ["pulse"]:
        return cmd_workflow(rest)       # a hook's pulse: silent and exit 0 whatever the platform or Python
    if _native_windows() and cmd not in ("doctor",):
        _die(f"container-map {cmd}: Windows is not supported natively (the thread engine needs "
             "fcntl file locks and POSIX process groups, and serve and demo run under bash). "
             "Run it inside WSL2: install a Linux distribution with `wsl --install`, then "
             "follow the Linux steps there", 1)
    static_demo = cmd == "demo" and "--static" in rest[:rest.index("--") if "--" in rest else None]
    floor = STATIC_DEMO_PYTHON if static_demo else MIN_PYTHON
    if cmd not in ("doctor", "thread") and sys.version_info[:2] < floor:
        v = sys.version_info                # doctor reports it, thread says it in its own format
        _die(f"container-map {cmd}: python {_floor(floor)}+ is needed (this is {v[0]}.{v[1]}); "
             f"`{_me()} doctor` says what else is missing", 1)
    try:
        return COMMANDS[cmd](rest)
    except KeyboardInterrupt:      # render, config and demo end the same way serve does
        print(f"container-map: {cmd} interrupted", file=sys.stderr, flush=True)
        return 130


if __name__ == "__main__":
    sys.exit(main())
