#!/usr/bin/env python3
"""tasks.py — T3-style tasks: say what you want, pick a folder and an agent, watch it work, review
the diff, then merge it or set it aside. One task = one directory of receipts:

  <tasks_dir>/<id>/task.json      what was asked, where, by which runner (written once)
  <tasks_dir>/<id>/events.jsonl   append-only: run, exit, stop, merged, discarded, done
  <tasks_dir>/<id>/run-N.log      run N's output, verbatim; prompt-N.txt is what it was given

Status is derived from the events, never stored. Every run goes through this file's own --exec
mode, a detached supervisor that waits on the agent, enforces the time cap and appends the exit
event itself, so the exit code survives a bridge restart. In a git repo the task works in its own
worktree on branch cm/<id>, cut from the last commit; merge brings that branch into the branch it
started from (git refuses to overwrite your uncommitted work), discard commits whatever is left
onto the branch and removes the worktree, so nothing is ever deleted. Build junk the agent left
untracked (merge_skip: __pycache__, node_modules, ...) is not committed; the receipt names it. Files
git would delete with the worktree that are not build byproducts keep the whole worktree: it moves
aside to <worktree>.retired-<stamp> instead, and so does a worktree git cannot remove.

The worktree lives outside the receipts, in <tasks_dir>-worktrees/<id> (config worktrees_dir): an
agent's ../ is then not the thread's task.json and events.jsonl. Agents run as your user, so this
stops accidents and casual forgery; a determined agent can still find the receipts by their path.
"""
from __future__ import annotations

import calendar
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

try:
    import fcntl
except ImportError:                     # Windows has no flock: the store is not supported there
    fcntl = None

TASKS_PY = os.path.abspath(__file__)
MAX_TEXT = 8000
MAX_VERIFY = 400              # a verify command: one shell line that must exit 0 for the work to count
MAX_RETRIES = 3
VERIFY_TIMEOUT = 600
SETUP_TIMEOUT = 600           # a fresh worktree's setup line (npm ci, a venv) before the first run
CHECK_MINUTES = 10            # a check run's cap is min(max_minutes, this) unless the runner sets check_minutes
# who signs a merge or a leftover commit when the repo's git config names nobody (a fresh machine)
FALLBACK_IDENTITY = (("user.name", "container-map"), ("user.email", "container-map@localhost"))
MAX_RUNNING = 4
MAX_TASKS_LISTED = 200        # closed threads kept on the list; an open one is never cut off
LOCK_WAIT = 600.0             # seconds to wait for another process's store or repository lock
RENAME_WAIT = 10.0            # seconds a rename waits for a thread another actor holds
LAUNCH_WAIT = 30.0            # seconds a supervisor waits for its run to be on record before it starts anything
TERM_GRACE, KILL_GRACE, REAP_GRACE = 15, 5, 5     # _end_group: seconds each step waits
LOG_CHUNK = 64 * 1024
MAX_PATCH = 200 * 1024
GIT_TIMEOUT = 60
# Given to a git run while the repository is locked (_git, hold in _lock_file): housekeeping that
# detaches (gc --auto, maintenance) would carry the lock's descriptor into the background and keep
# the repository locked for as long as it runs
GIT_NO_DETACH = ["-c", "gc.auto=0", "-c", "maintenance.auto=false"]
GIT_KEEP = ("GIT_ASKPASS", "GIT_TERMINAL_PROMPT")     # with GIT_SSH*, the only GIT_ variables kept
# what an agent, a check, a verify or a setup line never inherits from the map, by name: the
# bridge's own variables (BRIDGE_LIVE_PATH names the file that holds the agent token), the ones that
# aim the tool at a map (CONTAINER_CONFIG, CONTAINER_ROOT, CONTAINER_OUT ...) and the git variables
# that aim a command at one repository, index or object store. Names, not prefixes: a person's
# GIT_AUTHOR_*, GIT_COMMITTER_*, GIT_CONFIG_*, GIT_SSL_*, GIT_HTTP_* and GIT_EXEC_PATH, or the
# CONTAINER_* of a remote podman, are theirs and arrive. test_tasks checks this list against the
# names the tool's own sources read, so a new BRIDGE_/CONTAINER_ variable cannot be forgotten here.
MAP_ENV = ("BRIDGE_HOST", "BRIDGE_PORT", "BRIDGE_LIVE_PATH", "BRIDGE_LOG_PATH", "BRIDGE_GAME_PATH",
           "BRIDGE_RENDER_PATH", "BRIDGE_SEATS_PATH", "BRIDGE_SYNCED_DIR",
           "CONTAINER_CONFIG", "CONTAINER_ROOT", "CONTAINER_OUT", "CONTAINER_RENDER_SECONDS",
           "CONTAINER_TMUX_SESSION", "CONTAINER_WORK_ROOT", "CONTAINER_AREAS_JSON",
           "CONTAINER_LEGACY_IDS", "BRIDGE_UI_LOCK", "BRIDGE_LAUNCH_KEY")
GIT_REPO_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_PREFIX")
AGENT_ENV_DROP = frozenset(MAP_ENV + GIT_REPO_ENV)
# The engine's own git calls run with the repository's git hooks off (_git; only `worktree add`, the
# repository's own checkout, keeps them): a failing pre-commit hook must not make a thread
# unmergeable, and a hook an agent planted in the shared .git (it runs as you) must not run when you
# merge, look at a diff or list a worktree (a status refreshes the index, and that runs
# post-index-change). core.hooksPath points at a path that is not a folder, so no hook can be put
# there; --no-verify says the same to commit and merge.
NO_HOOKS = ["-c", f"core.hooksPath={os.devnull}"]
CREATE_MIN_GAP = 2.0
TASK_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]{0,32}-[0-9a-f]{6}$")
RUNNER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/+-]{0,47}$")
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
REGEN_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".cache", "dist", "build", "target", ".git",
              ".next", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox"}
# gitignore-style patterns: untracked files matching one are not committed onto a thread's branch
# when its leftovers are (a tracked file is always committed). The config's merge_skip replaces it.
DEFAULT_MERGE_SKIP = ["__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", "node_modules/",
                      ".DS_Store", ".mimocode/", "*.egg-info/", ".venv/"]
MAX_SKIP_PATTERNS = 100
MAX_SKIP_SHOWN = 20           # paths a receipt or a message names; the rest are counted
CLOSED = ("merged", "discarded", "done")
# what the store acts on (paths, git refs, the lines it runs as you, the retry budget): digested
# into the first run event at creation, so a record changed later is refused (_trusted)
PINNED = ("id", "target", "cwd", "mode", "repo", "branch", "base_sha", "base_branch", "worktree",
          "verify", "setup", "retries")
VERDICT_RE = re.compile(r"VERDICT:\s*(PASS|FAIL)\b", re.I)
# a verdict counts only as a line of its own: a quote inside a sentence ("... hardcodes
# VERDICT: PASS"), the prompt's instruction echoed back or a tool's output is not the
# checker's word. Markdown around it is allowed: a heading, bullet or quote mark before it
# (## - * > _) and emphasis (**VERDICT: PASS**).
VERDICT_LINE_RE = re.compile(r"^[ \t#>*_`-]*VERDICT:[ \t*_`]*(PASS|FAIL)\b", re.I | re.M)
VERDICT_TAIL = 128 * 1024              # how much of a check's log is read for its final answer
# the merge blockers for work edited after the evidence was taken: each digest is compared with
# the worktree as it is now, by itself
CHANGED_VERIFY = "the work changed after verify"
CHANGED_CHECK = "the work changed after check"
# a receipt that tried to take its digest and could not (git failed that moment) proves nothing
# about the content; an older receipt that has no digest field at all is not held to one
UNPROVEN = {CHANGED_VERIFY: "the work could not be fingerprinted at verify, so a change after it "
                            "cannot be ruled out",
            CHANGED_CHECK: "the work could not be fingerprinted at check, so a change after it "
                           "cannot be ruled out"}
TRAILER_RE = re.compile(r"^\s*(co-authored-by:|generated with|🤖)", re.I | re.M)
SUPERVISOR_LINE = re.compile(r"^# run [0-9]+ · ")       # the supervisor's own header and footer lines
MAX_PARSE_LINE = 256 * 1024
PRESAVE_MAX_FILES = 200
PRESAVE_MAX_BYTES = 20 * 1024 * 1024

# Zero-config: agent CLIs found on PATH become runners when the config names none. Each takes the
# prompt on stdin (or as {text}) and works in its current directory. followup_cmd continues the
# newest session in that directory, which is this task's own worktree.
# newest session in that directory, which is this task's own worktree. "models" are the choices the
# composer offers for that runner: each adds its args (before the stdin "-" or the {text} slot) and
# its env to the run. A runner with no models runs as is.
CLAUDE_MODELS = [{"name": "sonnet", "label": "Claude Sonnet (latest)", "args": ["--model", "sonnet"], "desc": "balanced (default)"},
                 {"name": "haiku", "label": "Claude Haiku (latest)", "args": ["--model", "haiku"], "desc": "fastest, cheapest"},
                 {"name": "opus", "label": "Claude Opus (latest)", "args": ["--model", "opus"], "desc": "strongest"}]
ROLES = ("both", "work", "check")      # check = reviews only (a read-only lane), never handed the work itself
# A second opinion counts only from another model family (workflows: check_runner). The detected runners by
# name; any other runner by what it is configured to run (its default model, its models' names and args, its
# command), else it is a family of its own. A family is a maker, never a product: two Claude models are one.
FAMILY = {"claude": "anthropic", "codex": "openai", "gemini": "google"}
FAMILY_HINTS = (("anthropic", ("claude", "sonnet", "opus", "haiku", "anthropic")),
                ("openai", ("codex", "gpt", "openai", "chatgpt")),
                ("google", ("gemini", "google")),
                ("xai", ("grok", "xai")),
                ("deepseek", ("deepseek",)),
                ("moonshot", ("kimi", "moonshot")),
                ("alibaba", ("qwen", "dashscope")),
                ("minimax", ("minimax",)),
                ("mistral", ("mistral", "codestral", "devstral")),
                ("meta", ("llama",)))
ORIGIN_MAX = 1024             # bytes of JSON an origin may take (workflows: {workflow, step, attempt})
RUN_ENV_RE = re.compile(r"^CM_WORKFLOW(?:_[A-Z]+)*$")    # the only variables create(env=) may add to a run
# "{session}" in a followup_cmd is replaced by the maker's own session id (read from its first run's
# output), so a check by the same agent in between never hijacks the continuation. check_cmd is the
# read-only variant a reviewer runs with; a runner without one is not offered as a checker.
DETECT = (
    ("claude", "Claude Code, headless: edits files, never stops to ask",
     ["claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits"],
     ["claude", "-p", "--resume", "{session}", "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits"],
     CLAUDE_MODELS,
     ["claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "plan", "--disallowedTools", "Edit,Write,NotebookEdit,MultiEdit"]),
    ("codex", "Codex CLI, sandboxed to the task folder",
     ["codex", "exec", "--json", "--sandbox", "workspace-write", "--skip-git-repo-check", "-"],
     ["codex", "exec", "--json", "--sandbox", "workspace-write", "--skip-git-repo-check", "resume", "{session}", "-"],
     [{"name": "default", "desc": "the model in its own config"}],
     ["codex", "exec", "--json", "--sandbox", "read-only", "--skip-git-repo-check", "-"]),
    ("gemini", "Gemini CLI, auto-approves edits",
     ["gemini", "--approval-mode", "auto_edit", "-o", "stream-json"], None,
     [{"name": "default", "desc": "the model in its own config"}], None),
    ("aider", "aider, commits as it goes",
     ["aider", "--yes-always", "--no-pretty", "--message", "{text}"], None,
     [{"name": "default", "desc": "the model in its own config"}], None),
)
SESSION_RE = re.compile(r'"(?:session_id|thread_id)"\s*:\s*"([A-Za-z0-9_-]{8,80})"')


_SCOPE: dict = {}
_SCOPE_LOCK = threading.Lock()          # the systemd-run probe runs once, not per racing request


def scope_prefix(tid: str, n: int, which=shutil.which) -> list[str]:
    """Run a thread's supervisor in its own systemd scope when the user manager is reachable, so a
    restart of the map's service (which kills its whole cgroup) never kills a running thread.
    Checked once per process; without systemd-run the supervisor runs as before."""
    with _SCOPE_LOCK:
        if "exe" not in _SCOPE:
            exe, ok = which("systemd-run"), False
            if exe and os.environ.get("XDG_RUNTIME_DIR"):
                try:
                    ok = subprocess.run([exe, "--user", "--scope", "--quiet", "--collect", "true"],
                                        capture_output=True, stdin=subprocess.DEVNULL,
                                        timeout=10).returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    ok = False
            _SCOPE["exe"] = exe if ok else ""
        if not _SCOPE["exe"]:
            return []
    unit = re.sub(r"[^a-z0-9-]", "", f"cm-task-{tid[-6:]}-{n}-{secrets.token_hex(3)}")
    return [_SCOPE["exe"], "--user", "--scope", "--quiet", "--collect", f"--unit={unit}", "--"]


class TaskError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _hinted_family(texts) -> str | None:
    for text in texts:
        low = str(text or "").lower()
        if not low:
            continue
        for family, words in FAMILY_HINTS:
            if any(w in low for w in words):
                return family
    return None


def _model_texts(m: dict) -> list:
    return [m.get("name"), " ".join(map(str, m.get("args") or [])),
            " ".join(map(str, (m.get("env") or {}).values()))]


def family_of(runner: object, runners: dict | None = None, model: object = None) -> str:
    """The model family a runner belongs to, else "runner:<name>": a runner nothing names is a family
    of its own. `model` (the model a task picked from the runner's list) decides first when what it
    runs names a family: a runner that defaults to one maker and is asked for another's model runs
    that other maker. Then FAMILY by name, then FAMILY_HINTS over what the runner runs."""
    name = runner if isinstance(runner, str) else ""
    spec = (runners or {}).get(name) if isinstance(runners, dict) else None
    spec = spec if isinstance(spec, dict) else {}
    if isinstance(model, str) and model:
        for m in spec.get("models") or []:
            if isinstance(m, dict) and m.get("name") == model:
                picked = _hinted_family(_model_texts(m))
                if picked:
                    return picked
    if name in FAMILY:
        return FAMILY[name]
    texts = [spec.get("default_model")]
    for m in spec.get("models") or []:
        if isinstance(m, dict):
            texts += _model_texts(m)
    cmd = spec.get("cmd") if isinstance(spec.get("cmd"), list) else []
    texts += [os.path.basename(str(a)) for a in cmd[:3]] + [name]
    return _hinted_family(texts) or "runner:" + name


def same_family(a: object, b: object, runners: dict | None = None, model_a: object = None,
                model_b: object = None) -> bool:
    """True when two runners (each with the model it runs, if picked) are one model family: a second
    opinion between them is no second opinion."""
    return family_of(a, runners, model_a) == family_of(b, runners, model_b)


def stamp(epoch: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if epoch is None else epoch))


def detect_runners(which=shutil.which) -> dict:
    out = {}
    for name, desc, cmd, follow, models, check in DETECT:
        exe = which(cmd[0])
        if exe:
            models = valid_models(models)
            out[name] = {"cmd": [exe] + cmd[1:], "followup_cmd": ([exe] + follow[1:]) if follow else None,
                         "check_cmd": ([exe] + check[1:]) if check else None,
                         "max_minutes": 30.0, "check_minutes": float(min(30, CHECK_MINUTES)),
                         "desc": desc, "detected": True, "role": "both" if check else "work", "label": name,
                         "models": models, "default_model": models[0]["name"] if models else None}
    return out


def valid_models(raw) -> list[dict]:
    """[{name, desc, args, env}] in the order offered; a bare string is a name with nothing attached.
    Anything malformed (or a repeated name) is left out. env values are plain strings the runner
    reads (a model pin), never a secret: they are shown in the task's receipt."""
    out, seen = [], set()
    if not isinstance(raw, list):
        return out
    for m in raw[:24]:
        if isinstance(m, str):
            m = {"name": m}
        if not isinstance(m, dict):
            continue
        name, args, env = m.get("name"), m.get("args") or [], m.get("env") or {}
        if not (isinstance(name, str) and MODEL_RE.fullmatch(name) and name not in seen):
            continue
        if not (isinstance(args, list) and all(isinstance(a, str) and a for a in args)):
            continue
        if not (isinstance(env, dict) and all(isinstance(k, str) and ENV_KEY_RE.fullmatch(k) and isinstance(v, str)
                                               and "\x00" not in v for k, v in env.items())):
            continue
        seen.add(name)
        label = m.get("label") if isinstance(m.get("label"), str) and m.get("label").strip() else name
        out.append({"name": name, "label": label.strip()[:60], "desc": str(m.get("desc", ""))[:120], "args": list(args), "env": dict(env)})
    return out


def with_model_args(argv: list[str], args: list[str]) -> list[str]:
    """A model's args go before the stdin marker "-" or the {text} slot, else at the end: the
    runner's own subcommand and flags stay first, the prompt stays last."""
    if not args:
        return list(argv)
    for i, a in enumerate(argv):
        if i and (a == "-" or "{text}" in a):
            if "{text}" in a and i > 1 and argv[i - 1].startswith("-"):
                i -= 1                      # keep `--message {text}` together
            return argv[:i] + list(args) + argv[i:]
    return list(argv) + list(args)


def valid_runners(raw) -> dict:
    """name → {cmd, followup_cmd, check_cmd, max_minutes, check_minutes, desc, models, default_model};
    anything malformed is left out."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for name, spec in raw.items():
        if not (isinstance(name, str) and RUNNER_RE.fullmatch(name) and isinstance(spec, dict)):
            continue
        cmd, follow, check = spec.get("cmd"), spec.get("followup_cmd"), spec.get("check_cmd")
        if not (isinstance(cmd, list) and cmd and all(isinstance(a, str) and a for a in cmd)):
            continue
        if follow is not None and not (isinstance(follow, list) and follow and all(isinstance(a, str) and a for a in follow)):
            follow = None
        if check is not None and not (isinstance(check, list) and check and all(isinstance(a, str) and a for a in check)):
            check = None
        minutes = spec.get("max_minutes", 30)
        if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or not 0 < minutes <= 24 * 60:
            continue
        check_minutes = spec.get("check_minutes")         # optional; a malformed one falls back to the default
        if not isinstance(check_minutes, (int, float)) or isinstance(check_minutes, bool) or not 0 < check_minutes <= 24 * 60:
            check_minutes = min(minutes, CHECK_MINUTES)
        models = valid_models(spec.get("models"))
        default = spec.get("default_model")
        if not (isinstance(default, str) and any(m["name"] == default for m in models)):
            default = models[0]["name"] if models else None
        role = spec.get("role") if spec.get("role") in ROLES else "both"
        if role == "check" and not check:
            check = cmd                                  # a pure reviewer's cmd is its check command
        if role != "work" and not check:
            role = "work"                                # no read-only command: it does work, it does not review
        out[name] = {"cmd": [os.path.expanduser(a) if a.startswith("~/") else a for a in cmd],
                     "followup_cmd": [os.path.expanduser(a) if a.startswith("~/") else a for a in follow] if follow else None,
                     "check_cmd": [os.path.expanduser(a) if a.startswith("~/") else a for a in check] if check else None,
                     "max_minutes": float(minutes), "check_minutes": float(check_minutes), "desc": str(spec.get("desc", ""))[:200],
                     "detected": bool(spec.get("detected")), "models": models, "default_model": default, "role": role,
                     "label": str(spec.get("label") or name)[:40]}
    return out


def runners_for(cfg: dict, which=shutil.which) -> dict:
    """The config's runners; with none configured (and detection on), the agent CLIs on PATH."""
    configured = valid_runners(cfg.get("runners") if isinstance(cfg.get("runners"), dict) else {})
    if configured or not cfg.get("detect_runners", True):
        return configured
    return detect_runners(which)


def public_runners(runners: dict) -> list[dict]:
    return [{"name": k, "desc": v.get("desc", ""), "detected": bool(v.get("detected")),
             "followup": bool(v.get("followup_cmd")), "max_minutes": v.get("max_minutes"),
             "models": [{"name": m["name"], "label": m.get("label") or m["name"], "desc": m.get("desc", "")} for m in v.get("models", [])],
             "default_model": v.get("default_model"), "role": v.get("role", "both"), "label": v.get("label") or k} for k, v in runners.items()]


def _git_env(extra: dict | None = None) -> dict:
    """The environment the engine's own git calls run in: the caller's, without its GIT_ variables
    (a bridge started from a hook or inside a repo command carries GIT_DIR, GIT_WORK_TREE,
    GIT_INDEX_FILE… that would point every call at the wrong repository) except GIT_SSH*,
    GIT_ASKPASS and GIT_TERMINAL_PROMPT. `extra` is laid on top, kept as given."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") or k.startswith("GIT_SSH") or k in GIT_KEEP}
    env.update(extra or {})
    return env


def agent_env(extra: dict | None = None, base: dict | None = None) -> dict:
    """The environment an agent, a check, a verify line or a setup line (or the wingmate's model
    command, or a hand-off to the owner's dispatch tool) runs in: the caller's (PATH, HOME, LANG,
    TERM, the agent's own login variables, a person's git identity, git config and TLS variables
    ...) without the map's own variables (MAP_ENV) and the git variables that aim a command at one
    repository (GIT_REPO_ENV), which a bridge started from a hook carries and which would send
    the agent's git at the wrong one. `extra` (a runner model's env) is laid on top as configured,
    so a variable a person sets for a runner always arrives."""
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if k not in AGENT_ENV_DROP}
    env.update(extra or {})
    return env


def _unencodable(value: str) -> bool:
    """Text that cannot be written as UTF-8 (a lone surrogate, which JSON can carry in as \\ud800
    and a shell argument can carry in as a byte that is not UTF-8)."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


_HELD = threading.local()      # the repository-lock descriptors this thread holds (_lock_file hold=True)


def _git(args: list[str], cwd: str, timeout: float = GIT_TIMEOUT, env: dict | None = None,
         stdin: str | None = None, errors: str = "strict", hooks: bool = False) -> subprocess.CompletedProcess:
    """git in `cwd`; `env` are variables to add to the scrubbed environment (_git_env); `stdin`
    is text for git to read; `errors` is how bytes that are not UTF-8 are read ("replace" for text
    shown to a person: a file in another encoding must not end the diff in an exception). The
    repository's file-system monitor is off for every call (it runs a command named in the config
    on each status, add or diff) and so are its hooks, unless `hooks` (a worktree's own checkout).
    While this thread holds the repository's lock, git inherits the lock's descriptor: the lock is
    then held for as long as the git run lives, also when the process that took it is killed (a
    flock goes with the last copy of its descriptor). Not for a run with hooks: a hook is the
    repository's own program and may leave a daemon behind, which would hold the lock as long."""
    held = () if hooks else getattr(_HELD, "fds", ())
    try:
        return subprocess.run(["git", "-c", "core.fsmonitor=false", *([] if hooks else NO_HOOKS),
                               *(GIT_NO_DETACH if held else []), *args],
                              cwd=cwd, capture_output=True,
                              text=True, errors=errors, timeout=timeout,
                              stdin=subprocess.DEVNULL if stdin is None else None, input=stdin,
                              env=_git_env(env), pass_fds=held)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["git", *args], 124, "", str(exc))


_COMMON_DIRS: dict = {}


def _repo_common(repo: str) -> str | None:
    """The real path of `repo`'s git common dir, the one every worktree of it shares; None when git
    cannot say. Asked of the repository itself, never of a worktree (whose .git an agent can edit)."""
    hit = _COMMON_DIRS.get(repo)
    if hit and os.path.isdir(hit):
        return hit
    got = _git(["rev-parse", "--git-common-dir"], repo, timeout=10)
    line = got.stdout.strip() if got.returncode == 0 else ""
    if not line:
        return None
    hit = os.path.realpath(line if os.path.isabs(line) else os.path.join(repo, line))
    _COMMON_DIRS[repo] = hit
    return hit


def _worktree_unsafe(wt: str, repo: str) -> str | None:
    """None while `wt/.git` is the file git wrote for this worktree: it names a folder inside
    `repo`'s own git directory (<common>/worktrees/<name>) and that folder names the file back. Else
    one sentence saying what is wrong. An agent can write its worktree, .git included; pointed at
    a copy of the repository it made, every git the map runs there would read that copy's config
    (a core.fsmonitor or filter command) and run it as you, outside the agent's sandbox and time
    cap. So the map runs no git in a worktree that fails this, and says so."""
    gitfile = os.path.join(wt, ".git")
    try:
        if os.path.islink(gitfile) or not os.path.isfile(gitfile):
            return "the worktree's .git is no longer the file git made for it"
        with open(gitfile, "rb") as handle:
            raw = handle.read(4097)
    except OSError as exc:
        return f"the worktree's .git cannot be read ({exc})"
    line = raw.decode("utf-8", "replace").strip()
    if len(raw) > 4096 or "\n" in line or not line.startswith("gitdir:"):
        return "the worktree's .git is not a one-line gitdir pointer"
    named = line[len("gitdir:"):].strip()
    admin = os.path.realpath(named if os.path.isabs(named) else os.path.join(wt, named))
    common = _repo_common(repo)
    if not common:
        return f"git cannot say where {repo} keeps its worktrees"
    if os.path.dirname(admin) != os.path.join(common, "worktrees"):
        return (f"the worktree's .git points at {admin}, which is not one of the folders git keeps "
                f"for {repo}'s worktrees")
    try:
        with open(os.path.join(admin, "gitdir"), encoding="utf-8", errors="replace") as handle:
            back = handle.read().strip()
    except OSError:
        return f"git's record of this worktree ({admin}) names no .git file"
    back = back if os.path.isabs(back) else os.path.join(admin, back)
    if os.path.realpath(back) != os.path.realpath(gitfile):
        return f"git's record of this worktree ({admin}) names another .git file"
    return None


def _unsafe_text(why: str) -> str:
    return (why + ". An agent could have written it, so no git is run in that folder: discard sets "
                  "it aside unread, merge is refused")


_MERGE_NO_VERIFY: list = []


def _merge_no_verify() -> list[str]:
    """["--no-verify"] when this git's merge knows the flag (git 2.24+), else []: the hooks-off
    path (NO_HOOKS) holds either way, and an older git must not fail every merge over a flag."""
    if not _MERGE_NO_VERIFY:
        got = _git(["merge", "-h"], tempfile.gettempdir(), timeout=10)
        _MERGE_NO_VERIFY.append(["--no-verify"] if "--no-verify" in (got.stdout + got.stderr)
                                else [])
    return list(_MERGE_NO_VERIFY[0])


def _identity(cwd: str) -> list[str]:
    """`-c user.name=… -c user.email=…` for whatever identity git has no config for here, so a
    merge or a leftover commit still lands on a machine nobody set git up on; [] when both are set."""
    out = []
    for key, value in FALLBACK_IDENTITY:
        got = _git(["config", key], cwd, timeout=10)
        if got.returncode != 0 or not got.stdout.strip():
            out += ["-c", f"{key}={value}"]
    return out


def _check_line(value: object, name: str) -> str | None:
    """One shell line (a verify or a setup command): plain text under MAX_VERIFY characters."""
    if value in (None, ""):
        return None
    if (not isinstance(value, str) or len(value) > MAX_VERIFY or any(ord(c) < 32 for c in value)
            or not value.strip()):
        raise TaskError(400, f"{name} must be one shell line under {MAX_VERIFY} characters")
    if _unencodable(value):
        raise TaskError(400, f"{name} must be valid text (it holds a character that cannot be "
                             "written as UTF-8, such as a lone surrogate)")
    return value.strip()


def valid_setup_map(raw) -> dict:
    """worktree_setup from the config: {absolute repo path or "*": one shell line}. Paths are
    resolved; anything malformed is left out."""
    out = {}
    for key, line in raw.items() if isinstance(raw, dict) else []:
        try:
            line = _check_line(line, "setup")
        except TaskError:
            continue
        if not (line and isinstance(key, str)):
            continue
        if key == "*":
            out["*"] = line
        elif os.path.isabs(os.path.expanduser(key)):
            out[os.path.realpath(os.path.expanduser(key))] = line
    return out


def _pin(task: dict) -> str:
    blob = json.dumps({k: task.get(k) for k in PINNED}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _first_lines(text: str, n: int = 6) -> str:
    return "\n".join([l for l in str(text or "").strip().splitlines() if l.strip()][:n])[:600]


def _slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:5]
    return "-".join(words)[:24].strip("-")


def _cmdline(pid: int, proc: str = "/proc") -> str:
    try:
        with open(f"{proc}/{pid}/cmdline", "rb") as handle:
            return handle.read().decode("utf-8", "replace").replace("\x00", " ")
    except OSError:
        pass
    try:                                     # no /proc (macOS): ask ps
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=3)
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _pid_alive(pid, tid: str, proc: str = "/proc") -> bool:
    """Our supervisor for this task is alive. A recycled pid, or one we cannot identify, is not."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    cmd = _cmdline(pid, proc)
    return "--exec" in cmd and tid in cmd


def _has_proc(proc: str = "/proc") -> bool:
    """A process filesystem to read (Linux). macOS has none, so ps answers instead."""
    return os.path.isdir(proc)


def _ps_rows() -> list[tuple[int, int, str]] | None:
    """(pid, pgid, state) of every process, from `ps -axo pid=,pgid=,stat=` (the same flags on
    macOS and Linux); None when ps cannot be run."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,pgid=,stat="], capture_output=True, text=True,
                             timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    rows = []
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


def _ps_cwds(pids) -> dict[int, str]:
    """{pid: the folder it works in} through lsof (which ships with macOS); {} where lsof is
    missing or fails. lsof exits 1 when one of the pids has gone, and still prints the rest."""
    exe = shutil.which("lsof")
    if not pids or not exe:
        return {}
    try:
        out = subprocess.run([exe, "-w", "-a", "-d", "cwd", "-Fpn", "-p", ",".join(map(str, pids))],
                             capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    found, pid = {}, None
    for line in out.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            found[pid] = line[1:]
    return found


def _proc_group(pgid: int, proc: str, with_cwd: bool) -> list[tuple[int, str]]:
    out = []
    try:
        names = [n for n in os.listdir(proc) if n.isdigit()]
    except OSError:
        return out
    for name in names:
        try:
            with open(f"{proc}/{name}/stat", "rb") as handle:
                stat = handle.read().decode("utf-8", "replace")
            fields = stat[stat.rindex(")") + 2:].split()      # state ppid pgrp ...
            if fields[0] in ("Z", "X", "x") or int(fields[2]) != pgid:
                continue                                     # a zombie writes nothing
            cwd = ""
            if with_cwd:
                cwd = os.readlink(f"{proc}/{name}/cwd")
                if cwd.endswith(" (deleted)"):
                    cwd = cwd[:-len(" (deleted)")]
                cwd = os.path.realpath(cwd)
            out.append((int(name), cwd))
        except (OSError, ValueError, IndexError):
            continue
    return out


def _group_pids(pgid: int, proc: str = "/proc") -> list[int]:
    """The live processes in group `pgid`: read from /proc, or from ps where there is no /proc."""
    if _has_proc(proc):
        return [pid for pid, _ in _proc_group(pgid, proc, False)]
    rows = _ps_rows() or []
    return [pid for pid, pg, state in rows if pg == pgid and state[:1] not in ("Z", "X")]


def _group_members(pgid: int, proc: str = "/proc") -> list[tuple[int, str]]:
    """(pid, cwd) of every live process in group `pgid`. Where there is no /proc (macOS) the
    group comes from ps and the folders from lsof; a folder lsof cannot name reads "" and
    belongs to nobody."""
    if _has_proc(proc):
        return _proc_group(pgid, proc, True)
    pids = _group_pids(pgid, proc)
    where = _ps_cwds(pids) if pids else {}
    return [(pid, os.path.realpath(where[pid]) if pid in where else "") for pid in pids]


def _owned_group(pgid, cwd: str, proc: str = "/proc") -> bool:
    """A live process group with a member still working in this task's folder (needs /proc, or
    ps and lsof where there is none). The leader may already be gone while what it started keeps
    running."""
    if not isinstance(pgid, int) or isinstance(pgid, bool) or pgid <= 0:
        return False
    try:
        os.killpg(pgid, 0)
    except OSError:
        return False
    base = os.path.realpath(cwd)
    inside = base.rstrip("/") + "/"                  # "/" itself holds everything, not only "//…"
    return any(c and (c == base or c.startswith(inside)) for _, c in _group_members(pgid, proc))


class LockBusy(TaskError):
    """A lock another actor holds past the wait (or at once, when no wait was asked for)."""


_PATH_LOCKS: dict = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _flock(fd: int, deadline: float | None) -> bool:
    """An exclusive flock on fd; False when `deadline` (a time.monotonic() value) passes first.
    Without fcntl there is nothing to take, and the answer is yes."""
    if fcntl is None:
        return True
    if deadline is None:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return True
    delay = 0.005
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(delay)
            delay = min(delay * 2, 0.1)


@contextlib.contextmanager
def _lock_file(path: str, wait: float | None, what: str, hold: bool = False):
    """Hold an exclusive lock on the file `path`: other threads of this process queue on a
    threading lock, other processes on flock (a process that dies lets go of it). `wait` seconds
    (None = for ever, 0 = not at all), then LockBusy naming `what`. Without fcntl only the
    threads of this process are kept apart. `hold`: the git runs this thread makes meanwhile
    inherit the descriptor (see _git), so a git mutation still running keeps the lock after the
    process that began it is gone."""
    deadline = None if wait is None else time.monotonic() + wait
    with _PATH_LOCKS_GUARD:
        guard = _PATH_LOCKS.setdefault(path, threading.Lock())
    if not guard.acquire(timeout=-1 if wait is None else wait):
        raise LockBusy(503, f"{what} is busy: another thread of this process held it "
                            f"for {wait:g} s")
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if not _flock(fd, deadline):
                raise LockBusy(503, f"{what} is held by another map or command; waited {wait:g} s")
            if hold:
                _HELD.fds = (*getattr(_HELD, "fds", ()), fd)
            try:
                yield
            finally:
                if hold:
                    _HELD.fds = tuple(f for f in getattr(_HELD, "fds", ()) if f != fd)
                if fcntl is not None:
                    with contextlib.suppress(OSError):
                        fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    finally:
        guard.release()


def _locked(path: str, wait: float | None = None):
    """One actor at a time per task: the bridge's verbs and the supervisor's exit receipt."""
    return _lock_file(os.path.join(path, ".lock"), wait, "this thread")


def _porcelain_paths(out: str) -> list[str]:
    """Paths from `git status --porcelain=v1 -z`; a rename or copy entry is followed by its source."""
    parts, paths, i = out.split("\x00"), [], 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) > 3:
            paths.append(entry[3:])
            if entry[0] in "RC":
                i += 1
        i += 1
    return paths


def _add_kept(tree: str, stay: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    """Stage every tracked change (edits and deletions) and exactly the untracked files in `stay`,
    which is `add -A` minus what merge_skip leaves out. Run it in the repo's top folder: `stay` is
    a list of paths from there."""
    add = _git(["--literal-pathspecs", "add", "-u"], tree, env=env)
    if add.returncode == 0 and stay:
        add = _git(["--literal-pathspecs", "add", "--pathspec-from-file=-", "--pathspec-file-nul"],
                   tree, timeout=120, env=env, stdin="\x00".join(stay) + "\x00")
    return add


def _work_digest(tree: str, skip: list[str] | None = None) -> str | None:
    """What a merge of this worktree would land, as one id: the tree git builds after staging, in
    a scratch copy of the worktree's index, the HEAD tree plus every uncommitted, new and deleted
    path, the way a merge commits a thread's leftovers: git-ignored files are left out, and so are
    untracked files the `skip` patterns name (the config's merge_skip; default DEFAULT_MERGE_SKIP),
    because the merge leaves those off the branch too and a verify or check that runs the tests
    writes __pycache__ into a repo that has no .gitignore. The same content is the same id whether
    the agent committed it or not. The scratch index has its own object directory, so the repo is
    only read. None when git cannot say."""
    patterns = DEFAULT_MERGE_SKIP if skip is None else skip
    try:
        where = _git(["rev-parse", "--show-toplevel", "--git-path", "index", "--git-path",
                      "objects"], tree, timeout=10)
        paths = where.stdout.split("\n")
        if where.returncode != 0 or len(paths) < 3 or not paths[0]:
            return None
        top = paths[0]
        index, objects = (os.path.join(tree, p) for p in paths[1:3])
        with tempfile.TemporaryDirectory(prefix="cm-digest-") as scratch:
            mine = os.path.join(scratch, "objects")
            os.mkdir(mine)
            env = {"GIT_INDEX_FILE": os.path.join(scratch, "index"), "GIT_OBJECT_DIRECTORY": mine,
                   "GIT_ALTERNATE_OBJECT_DIRECTORIES": objects}
            try:                      # its stat cache makes `add` rehash only what changed
                shutil.copyfile(index, env["GIT_INDEX_FILE"])
            except OSError:
                _git(["read-tree", "HEAD"], top, env=env)
            if patterns:
                kept = _git(["ls-files", "--others", "--exclude-standard", "-z",
                             *[f"--exclude={p}" for p in patterns]], top, timeout=60, env=env)
                if kept.returncode != 0:
                    return None
                added = _add_kept(top, [p for p in kept.stdout.split("\x00") if p], env)
            else:
                added = _git(["add", "-A"], top, timeout=120, env=env)
            if added.returncode != 0:
                return None
            made = _git(["write-tree"], top, env=env)
            return (made.stdout.strip() or None) if made.returncode == 0 else None
    except (OSError, ValueError):
        return None


def _receipt_digest(row: dict) -> str | None:
    """The content digest a verify or check receipt carries: the id; "" when the receipt tried to
    take one and git could not (it proves nothing about the content); None when the receipt has
    no digest field at all (older, or an in-place thread's), which the merge gate does not hold
    to one."""
    if "digest" not in row:
        return None
    return row["digest"] or ""


def cap_text(seconds: object, lead: str = " (", tail: str = ")") -> str:
    """A time cap as a person reads it, wrapped for a sentence: " (30 min)", " (90 s)"; "" when
    the run on record names none."""
    try:
        total = float(seconds)
    except (TypeError, ValueError):
        return ""
    if not total > 0:
        return ""
    nice = f"{secs_text(total / 60)} min" if total >= 60 and total % 60 == 0 else f"{secs_text(total)} s"
    return f"{lead}{nice}{tail}"


def _unfinished(repo: str) -> str | None:
    """What the repo's owner is in the middle of (merge, cherry-pick, revert, rebase or am), from
    the files git leaves while one waits for them; None when nothing is."""
    kinds = (("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"),
             ("sequencer", "cherry-pick or revert"), ("rebase-merge", "rebase"),
             ("rebase-apply", "rebase or am"))
    where = _git(["rev-parse", *(a for name, _ in kinds for a in ("--git-path", name))], repo,
                 timeout=10)
    paths = where.stdout.split("\n")
    if where.returncode != 0 or len(paths) < len(kinds):
        return None
    return next((what for (_, what), path in zip(kinds, paths)
                 if os.path.exists(os.path.join(repo, path))), None)


def _regenerable(path: str) -> bool:
    """A build byproduct git already ignores: some part of the path is a REGEN_DIRS folder, an
    *.egg-info folder, .mimocode, a .DS_Store or a .pyc. Losing it with its worktree costs
    nothing."""
    return any(p in REGEN_DIRS or p == ".mimocode" or p.endswith((".egg-info", ".pyc"))
               or p == ".DS_Store" for p in path.split("/") if p)


def _default_junk(path: str) -> bool:
    """An untracked path the built-in merge_skip list names. Those are known junk, so they go with
    the worktree. A path only your own merge_skip names is not: you chose to keep it off the branch,
    not to lose it, so it stays in the worktree (_remove_worktree moves that aside)."""
    return _skip_hit(path, DEFAULT_MERGE_SKIP) is not None


def _leftovers(tree: str) -> dict | None:
    """What `git worktree remove` would delete from `tree` besides tracked files: ignored and
    untracked paths. {"kept": those nobody can regenerate (an ignored path that is not a build
    byproduct; an untracked one the built-in junk list does not name), "junk": there are removable
    byproducts too, "dirty": tracked files still differ from the branch}. None when git cannot
    say."""
    st = _git(["status", "--porcelain=v1", "-z", "--ignored=matching"], tree, timeout=30)
    others = _git(["ls-files", "--others", "--exclude-standard", "-z"], tree, timeout=60)
    if st.returncode != 0 or others.returncode != 0:
        return None
    ignored, dirty = [], False
    for entry in st.stdout.split("\x00"):
        if entry.startswith("!! "):
            ignored.append(entry[3:])
        elif len(entry) > 3 and not entry.startswith("?? "):     # untracked: listed whole below
            dirty = True
    untracked = [p for p in others.stdout.split("\x00") if p]
    kept = ([p for p in ignored if not _regenerable(p)]
            + [p for p in untracked if not _default_junk(p)])
    return {"kept": kept, "junk": len(kept) < len(ignored) + len(untracked), "dirty": dirty}


def _submodules(tree: str) -> list[str] | None:
    """The initialized submodules of the worktree `tree`: gitlinks in its index whose folder holds
    a checkout. `git worktree remove` refuses to take a worktree with one away, and --force
    removes the submodule's files too, ignored ones nobody can regenerate included, which the
    superproject's own status never lists (_leftovers). None when git cannot say."""
    got = _git(["ls-files", "--stage", "-z"], tree, timeout=30)
    if got.returncode != 0:
        return None
    out = []
    for entry in got.stdout.split("\x00"):
        meta, _, name = entry.partition("\t")
        if meta.startswith("160000 ") and name and os.path.lexists(os.path.join(tree, name, ".git")):
            out.append(name)
    return out


def valid_merge_skip(raw) -> list[str]:
    """merge_skip from the config: gitignore-style patterns, one line each. Anything that is not
    a list is the default; a list is kept as given minus what is malformed ([] = skip nothing)."""
    if not isinstance(raw, list):
        return list(DEFAULT_MERGE_SKIP)
    out = []
    for item in raw:
        if not isinstance(item, str):
            continue
        pat = item.strip()
        if pat and len(pat) <= 200 and not any(ord(c) < 32 for c in pat) and pat not in out:
            out.append(pat)
    return out[:MAX_SKIP_PATTERNS]


def _skip_hit(path: str, patterns: list[str]) -> str | None:
    """The shortest part of `path` a pattern names (a folder with its slash, or the file itself);
    None when no pattern is recognised (git's own matching decides what is skipped)."""
    parts = path.split("/")
    for i, name in enumerate(parts):
        is_dir, prefix = i < len(parts) - 1, "/".join(parts[:i + 1])
        for pat in patterns:
            if pat.startswith("!"):
                continue
            only_dir, pat = pat.endswith("/"), pat.strip("/")
            hit = fnmatch.fnmatchcase(prefix, pat) if "/" in pat else fnmatch.fnmatchcase(name, pat)
            if hit and (is_dir or not only_dir):
                return prefix + "/" if is_dir else prefix
    return None


def _skip_unit(path: str, patterns: list[str]) -> str:
    """What a receipt names for `path`: the folder a pattern names ("node_modules/" instead of
    its files), else the path itself."""
    return _skip_hit(path, patterns) or path


def _skip_units(paths: list[str], patterns: list[str]) -> dict:
    """{"count": files skipped, "paths": up to MAX_SKIP_SHOWN of what the patterns named}."""
    units = sorted({_skip_unit(p, patterns) for p in paths})
    return {"count": len(paths), "paths": units[:MAX_SKIP_SHOWN]}


def default_worktrees_dir(tasks_dir: str) -> str:
    """<tasks_dir>-worktrees, beside the receipts folder and not inside it."""
    return tasks_dir.rstrip("/") + "-worktrees"


# ---- reading an agent's log: Claude Code stream-json, Codex --json, or plain text ----------------
def _tool_summary(name: str, inp) -> str:
    inp = inp if isinstance(inp, dict) else {}
    name = str(name or "tool")
    for key in ("file_path", "path", "notebook_path"):
        if isinstance(inp.get(key), str):
            return f"{name} {inp[key][:240]}"
    if isinstance(inp.get("command"), str):
        return f"{name}: $ {inp['command'][:240]}"
    for key in ("pattern", "query", "url", "description", "prompt"):
        if isinstance(inp.get(key), str):
            return f"{name}: {inp[key][:160]}"
    return name


def parse_event(obj) -> list[dict]:
    if not isinstance(obj, dict):
        return [{"k": "raw", "s": json.dumps(obj)[:300]}]
    t = obj.get("type")
    if t == "assistant":                                         # Claude Code
        out = []
        content = (obj.get("message") or {}).get("content") if isinstance(obj.get("message"), dict) else None
        for c in content if isinstance(content, list) else []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text" and str(c.get("text", "")).strip():
                out.append({"k": "text", "s": str(c["text"]).strip()[:4000]})
            elif c.get("type") == "tool_use":
                out.append({"k": "tool", "s": _tool_summary(c.get("name"), c.get("input"))})
        return out
    if t == "user":
        content = (obj.get("message") or {}).get("content") if isinstance(obj.get("message"), dict) else None
        out = []
        for c in content if isinstance(content, list) else []:
            if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                body = c.get("content")
                body = body if isinstance(body, str) else json.dumps(body)[:300]
                out.append({"k": "err", "s": "tool error: " + body[:300]})
        return out
    if t == "result":
        secs = obj.get("duration_ms")
        bits = [str(obj.get("subtype") or "done")]
        if isinstance(obj.get("num_turns"), int):
            bits.append(f"{obj['num_turns']} turns")
        if isinstance(secs, (int, float)):
            bits.append(f"{secs / 1000:.0f}s")
        out = [{"k": "result", "s": "finished · " + " · ".join(bits)}]
        if isinstance(obj.get("result"), str) and obj["result"].strip():
            out.append({"k": "text", "s": obj["result"].strip()[:4000]})
        return out
    if t == "system":
        if obj.get("subtype") == "init":
            return [{"k": "info", "s": "session started" + (f" · model {obj['model']}" if obj.get("model") else "")}]
        return []
    if t in ("item.started", "item.completed", "item.updated"):  # Codex exec --json
        item = obj.get("item") if isinstance(obj.get("item"), dict) else {}
        ty = item.get("type") or item.get("item_type")
        done = t == "item.completed"
        if ty in ("agent_message", "assistant_message") and done:
            return [{"k": "text", "s": str(item.get("text", "")).strip()[:4000]}] if str(item.get("text", "")).strip() else []
        if ty == "reasoning" and done:
            text = str(item.get("text", "")).strip()
            return [{"k": "think", "s": text[:300]}] if text else []
        if ty == "command_execution":
            if not done:
                return [{"k": "cmd", "s": "$ " + str(item.get("command", ""))[:300]}]
            code = item.get("exit_code")
            return [{"k": "err", "s": f"exit {code}: {str(item.get('command', ''))[:200]}"}] if isinstance(code, int) and code != 0 else []
        if ty == "file_change" and done:
            changes = item.get("changes") if isinstance(item.get("changes"), list) else []
            names = [f"{c.get('kind', 'edit')} {c.get('path', '?')}" for c in changes if isinstance(c, dict)]
            return [{"k": "file", "s": ", ".join(names)[:400] or "files changed"}]
        if ty in ("mcp_tool_call", "web_search", "todo_list") and done:
            return [{"k": "tool", "s": str(ty) + ": " + str(item.get("tool") or item.get("query") or "")[:200]}]
        if ty == "error":
            return [{"k": "err", "s": str(item.get("message", "error"))[:400]}]
        return []
    if t in ("turn.completed",):
        return [{"k": "result", "s": "turn finished"}]
    if t in ("error", "turn.failed"):
        msg = obj.get("message") or (obj.get("error") or {}).get("message") if isinstance(obj.get("error"), dict) else obj.get("message")
        return [{"k": "err", "s": str(msg or t)[:400]}]
    if t in ("thread.started", "turn.started"):
        return [{"k": "info", "s": t.replace(".", " ")}] if t == "thread.started" else []
    return [{"k": "raw", "s": json.dumps(obj, separators=(",", ":"))[:300]}]


def parse_line(line: str) -> list[dict]:
    s = line.strip()
    if not s:
        return []
    if len(s) > MAX_PARSE_LINE:
        return [{"k": "raw", "s": s[:600]}]
    if SUPERVISOR_LINE.match(s):
        return [{"k": "meta", "s": s[2:][:300]}]
    if s.startswith("{"):
        try:
            return parse_event(json.loads(s))
        except ValueError:
            pass
    return [{"k": "raw", "s": s[:600]}]


def declares_events(argv: list[str] | None) -> bool:
    """Whether a runner's command line asks for an event stream: `--json` (Codex) or the format
    stream-json (`--output-format stream-json`, `-o stream-json`, `--output-format=stream-json`)."""
    return any(a == "--json" or a == "stream-json" or a.endswith("=stream-json")
               for a in argv or ())


def _json_object(line: str) -> dict | None:
    s = line.strip()
    if s.startswith("{"):
        try:
            obj = json.loads(s)
        except ValueError:
            return None
        return obj if isinstance(obj, dict) else None
    return None


def final_answer(log: str, events: bool | None = None) -> str:
    """What a checker said last, read from its run log: the `result` of a Claude Code stream-json
    run, else its last assistant message (Claude, Codex or Gemini events); output that is not
    events is taken as printed. A tool's output is never part of it.

    Whether the log is an event stream is decided once, not line by line: `events` is the
    runner's declared output format (see declares_events) when the caller knows it, else the log's
    first line (the supervisor's own header lines do not count): a stream's first line is an
    event. A plain-text checker that prints a JSON line among its prose is still plain text."""
    result = assistant = None
    plain, chunked = [], False
    if events is None:
        first = next((line for line in log.splitlines()
                      if line.strip() and not SUPERVISOR_LINE.match(line.strip())), "")
        events = _json_object(first) is not None
    for line in log.splitlines():
        s = line.strip()
        if SUPERVISOR_LINE.match(s):
            continue
        obj = _json_object(s) if events else None
        if obj is None:
            plain.append(line)
            continue
        kind, streaming = obj.get("type"), False
        if kind == "result" and isinstance(obj.get("result"), str):
            result = obj["result"]
        elif kind == "assistant":
            message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            texts = [c["text"] for c in message.get("content") or [] if isinstance(c, dict)
                     and c.get("type") == "text" and isinstance(c.get("text"), str)]
            assistant = "\n".join(texts) if texts else assistant
        elif kind == "item.completed" and isinstance(obj.get("item"), dict):
            item = obj["item"]
            ty = item.get("type") or item.get("item_type")
            if ty in ("agent_message", "assistant_message") and isinstance(item.get("text"), str):
                assistant = item["text"]
        elif kind == "message" and obj.get("role") == "assistant" \
                and isinstance(obj.get("content"), str):
            streaming = True                                   # Gemini: a reply arrives in chunks
            more = chunked and obj.get("delta")
            assistant = (assistant or "") + obj["content"] if more else obj["content"]
        chunked = streaming
    return (result or assistant or "") if events else "\n".join(plain)


class TaskStore:
    def __init__(self, tasks_dir: str, runners: dict, roots, python: str = sys.executable,
                 max_running: int = MAX_RUNNING, now=time.time, no_ai_trailers: bool = False, scope: bool = False,
                 worktree_setup=None, merge_skip=None, merge_requires_check: bool = False,
                 worktrees_dir: str | None = None, config_path: str | None = None):
        # the config file this store was built from ("" = none: zero-config, or a test's own
        # runners). A thread records it, so only a store of that file may call its agent gone
        # (_retry_blocker); a thread made with none has no store that may
        self.config_path = os.path.realpath(os.path.expanduser(config_path)) if config_path else ""
        self.verify_timeout = VERIFY_TIMEOUT    # seconds a verify / setup line may run (tests lower them)
        self.setup_timeout = SETUP_TIMEOUT
        self.scope = scope                      # supervisors in their own systemd scope (the bridge turns this on)
        self.worktree_setup = valid_setup_map(worktree_setup)   # config default setup line per repo, "*" for any
        self.merge_skip = valid_merge_skip(merge_skip)     # untracked paths a leftover commit omits
        self.merge_requires_check = bool(merge_requires_check)    # no fresh check PASS = a blocker
        self.tasks_dir = os.path.realpath(os.path.abspath(os.path.expanduser(tasks_dir)))
        # new worktrees go here, outside the receipts, so an agent's ../ is not a thread's task.json
        self.worktrees_dir = os.path.realpath(os.path.abspath(os.path.expanduser(
            worktrees_dir or default_worktrees_dir(self.tasks_dir))))
        self.runners = runners
        self.roots = tuple(os.path.realpath(r) for r in roots if r)
        self.python = python or "python3"
        self.max_running = max_running
        self.now = now
        # The bridge serves requests on threads, and the CLI is another process on the same
        # directory, so what two stores share is guarded by files, not by this object. Three locks,
        # taken in this order: a task's own (_locked, ".lock" in its folder), then the store-wide
        # capacity lock (_capacity, ".store.lock" in tasks_dir: held from the check for room until
        # the run is on record), then the repository's (_repo_lock, in its git common dir: held
        # across a whole mutation, a merge, a discard or a worktree add). Nobody holding a later
        # one waits for an earlier one; the one exception is create, which holds the capacity lock
        # and then takes the lock of the thread it is making, one nobody else can hold before its
        # task.json exists. _gate only spaces creates out in this process.
        self._gate = threading.RLock()       # last_create
        self._tick_lock = threading.Lock()   # one tick at a time (ticker thread and list route)
        self.tick_errors: list[tuple[str, str]] = []   # (id, message) of the last tick's failures
        self._tick_reported: set = set()     # what on_tick_error was already told
        self.on_tick_error = None            # callable(tid, message); the bridge audits through it
        self.last_create = 0.0
        self.no_ai_trailers = bool(no_ai_trailers)   # refuse to merge commits that carry AI attribution trailers
        self.reconcile_errors: list[tuple[str, str]] = []   # (id, message): could not be finished
        self.reconciled: list[tuple[str, str]] = self.reconcile()   # (id, outcome) finished at open

    # ---- reading -----------------------------------------------------------------------------
    def _dir(self, tid: object) -> str:
        if not isinstance(tid, str) or not TASK_ID_RE.fullmatch(tid):
            raise TaskError(400, "id must name a task")
        path = os.path.join(self.tasks_dir, tid)
        if not os.path.isfile(os.path.join(path, "task.json")):
            raise TaskError(404, "no such task")
        return path

    def _trusted(self, path: str, task: dict | None = None) -> dict:
        """task.json sits where an agent running as this user can write (a thread made before the
        worktrees left the receipts even has it one ../ away): before any
        operation, every stored path must still be where the bridge put it, and what the store
        acts on (PINNED) must match the digest the first run event took at creation. A thread
        from before the digest has none and is checked by its paths only. Returns the task."""
        task = self._read_task(path) if task is None else task
        tid = str(task.get("id") or "")
        home = os.path.join(self.tasks_dir, tid)
        target = os.path.realpath(str(task.get("target") or ""))
        if not TASK_ID_RE.fullmatch(tid) or not any(target == r or target.startswith(r.rstrip("/") + "/") for r in self.roots):
            raise TaskError(409, "this thread's record points outside the map; refusing to act on it")
        if tid != os.path.basename(path):
            raise TaskError(409, "this thread's record names another thread; refusing to act on it")
        cwd = os.path.realpath(str(task.get("cwd") or ""))
        wt = task.get("worktree")
        if task.get("mode") == "worktree":
            wt_real = os.path.realpath(str(wt or ""))
            # where it is now, or where threads made before the worktrees left the receipts put it
            places = (os.path.join(self.worktrees_dir, tid), os.path.join(home, "worktree"))
            if wt_real not in places or not (cwd == wt_real or cwd.startswith(wt_real + "/")):
                raise TaskError(409, "this thread's worktree record was altered; refusing to act on it")
        elif cwd != target:
            raise TaskError(409, "this thread's folder record was altered; refusing to act on it")
        first = next((e for e in self._events(path) if e["ev"] == "run" and e.get("n") == 1), None)
        if first and "pin" in first and first["pin"] != _pin(task):
            raise TaskError(409, "this thread's record was altered after it started (its repo, "
                                 "branch, verify, setup or retries); refusing to act on it")
        return task

    @staticmethod
    def _read_task(path: str) -> dict:
        with open(os.path.join(path, "task.json"), encoding="utf-8") as handle:
            task = json.load(handle)
        if not isinstance(task, dict):
            raise ValueError("task.json is not an object")
        return task

    @staticmethod
    def _events(path: str) -> list[dict]:
        out = []
        try:
            with open(os.path.join(path, "events.jsonl"), encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and isinstance(row.get("ev"), str):
                        out.append(row)
        except OSError:
            pass
        return out

    @staticmethod
    def _append(path: str, row: dict) -> None:
        line = (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")
        fd = os.open(os.path.join(path, "events.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)

    def _derive(self, task: dict, events: list[dict]) -> dict:
        runs, exits, closed, stop_asked, verifies, setups = {}, {}, None, set(), {}, {}
        blocks = {}
        for e in events:
            if e["ev"] == "run" and isinstance(e.get("n"), int):
                runs[e["n"]] = e
            elif e["ev"] == "exit" and isinstance(e.get("n"), int):
                exits[e["n"]] = e
            elif e["ev"] == "verify" and isinstance(e.get("n"), int):
                verifies[e["n"]] = e
            elif e["ev"] == "setup" and isinstance(e.get("n"), int):
                setups[e["n"]] = e
            elif e["ev"] == "stop" and isinstance(e.get("n"), int):
                stop_asked.add(e["n"])
            elif e["ev"] == "blocked" and isinstance(e.get("n"), int):
                blocks[e["n"]] = str(e.get("reason") or "the retry cannot go out")[:300]
            elif e["ev"] in CLOSED:
                closed = e
        last = max(runs) if runs else 0
        work = [n for n in runs if runs[n].get("kind") != "check"]          # the task's own runs
        lastw = max(work) if work else 0
        status, rc, verify, setup_failed, verify_digest = "new", None, None, False, None
        killed, cap = False, None
        if lastw:
            ex = exits.get(lastw)
            v = verifies.get(lastw)
            if v is not None:                   # shown whatever came after it (a lost supervisor)
                verify = {"n": lastw, "rc": v.get("rc"), "seconds": v.get("seconds"),
                          "at": v.get("at")}
                if v.get("timed_out"):
                    verify.update(timed_out=True, limit=v.get("limit"))
                verify_digest = _receipt_digest(v)
                if v.get("error"):
                    verify["error"] = str(v["error"])[:200]
            if ex:
                rc = ex.get("rc")
                killed, cap = bool(ex.get("killed")), runs[lastw].get("cap")
                setup_failed = bool(ex.get("setup_failed"))
                status = ("stopped" if ex.get("stopped") or lastw in stop_asked else
                          "failed" if ex.get("killed") or rc != 0 else "review")
                if v is not None:                           # the oracle's word: the work counts only when it passes
                    if status != "stopped" and not ex.get("killed"):   # whatever the agent's own exit code was
                        status = "review" if v.get("rc") == 0 else "failed"
                elif task.get("verify") and status == "review":
                    # configured, never ran (an older supervisor): unjudged work does not count
                    verify = {"n": lastw, "rc": None, "seconds": None, "at": None}
                    status = "failed"
            elif _pid_alive(runs[lastw].get("pid"), task["id"]):
                status = "stopping" if lastw in stop_asked else "running"
            else:
                status = "lost"
        check = None
        checks = [n for n in runs if runs[n].get("kind") == "check"]

        def finished(cn):                         # a check run that left its exit receipt
            cex = exits[cn]
            return {"n": cn, "runner": runs[cn].get("runner"), "model": runs[cn].get("model"),
                    "rc": cex.get("rc"), "at": cex.get("at"),
                    # the time cap ended it: whatever it printed, its exit code or a verdict line
                    # it managed to write on the way out are not an answer (see _verdict)
                    "killed": bool(cex.get("killed")), "cap": runs[cn].get("cap"),
                    # a stop of the check reads like one of the work
                    "verdict": "stopped" if cex.get("stopped") or cn in stop_asked else None,
                    "stale": cn < lastw}              # stale: work happened after the check
        if checks:
            cn = max(checks)
            asked = cn in stop_asked
            who = {"n": cn, "runner": runs[cn].get("runner"), "model": runs[cn].get("model")}
            if cn in exits:
                check = finished(cn)
            elif _pid_alive(runs[cn].get("pid"), task["id"]):
                check = who | {"rc": None, "verdict": "stopping" if asked else "checking",
                               "at": runs[cn].get("at")}
                if status not in CLOSED:
                    status = "stopping" if asked else "checking"
            else:
                check = {"n": cn, "runner": runs[cn].get("runner"), "model": runs[cn].get("model"), "rc": None, "verdict": "lost", "at": runs[cn].get("at")}
        if closed:
            status = closed["ev"]
        ended = exits.get(lastw, {}).get("at") if lastw else None
        work_runs = len(work)
        s = setups[max(setups)] if setups else None
        setup = {"n": s["n"], "rc": s.get("rc"), "seconds": s.get("seconds"), "at": s.get("at")} if s else None
        if setup and s.get("timed_out"):
            setup.update(timed_out=True, limit=s.get("limit"))
        # judged on the newest work run's verify: a check run after it (status "checking"
        # while it runs) neither re-arms nor cancels the retry
        due = bool(verify and verify.get("rc") not in (None, 0) and status == "failed"
                   and not closed and work_runs < 1 + int(task.get("retries") or 0))
        blocked = blocks.get(lastw) if due else None      # a later run of any kind leaves it behind
        # every check that finished, newest first, and the content digests the receipts carry:
        # what the merge gate weighs (held verdict, work changed since) beyond the newest check
        done = [check if n == max(checks) else finished(n)
                for n in sorted((n for n in checks if n in exits), reverse=True)]
        digests = {"verify": verify_digest,
                   "checks": {n: _receipt_digest(exits[n]) for n in checks if n in exits}}
        return {"status": status, "rc": rc, "runs": last, "started": runs[lastw].get("at") if lastw else None,
                "ended": ended, "closed": closed, "check": check, "verify": verify, "attempts": work_runs, "setup": setup,
                "checks_done": done, "digests": digests,
                "verify_set": bool(task.get("verify")), "setup_failed": setup_failed,
                "work_run": lastw, "killed": killed, "cap": cap,
                "retry_due": due and blocked is None, "blocked": blocked}

    def _verdict(self, path: str, check: dict | None) -> dict | None:
        """The checker's last word: an anchored `VERDICT: PASS|FAIL` line in its final answer (see
        final_answer), read from the end of its run log."""
        if check and check.get("rc") is not None and check.get("verdict") is None:
            if check.get("killed"):
                # the time cap ended it: not a verdict, even when it trapped the signal, printed
                # a VERDICT line and exited 0 on the way out
                check["verdict"] = "no verdict · timed out" + cap_text(check.get("cap"), " after ", "")
                return check
            if check.get("rc") != 0:
                # a reviewer that crashed did not pass anything
                check["verdict"] = f"no verdict · exit {check.get('rc')}"
                return check
            log = os.path.join(path, f"run-{check['n']}.log")
            try:
                with open(log, "rb") as handle:
                    start = max(0, os.path.getsize(log) - VERDICT_TAIL)
                    handle.seek(start)
                    text = handle.read().decode("utf-8", "replace")
                if start:
                    text = text.partition("\n")[2]         # the window began inside a line
                found = VERDICT_LINE_RE.findall(final_answer(text, self._declares_events(check)))
                check["verdict"] = found[-1].upper() if found else "no verdict"
            except OSError:
                check["verdict"] = "no verdict"
        return check

    def _declares_events(self, check: dict) -> bool | None:
        """True when the runner that made this check run asks for an event stream in its command
        line; None when it does not say (the log's first line decides, see final_answer)."""
        spec = self.runners.get(check.get("runner") or "") or {}
        return True if declares_events(spec.get("check_cmd") or spec.get("cmd")) else None

    def _read_verdicts(self, path: str, d: dict) -> dict:
        """Fill in the newest check's verdict and find the verdict the merge gate holds: the newest
        check that finished with a PASS or FAIL. A recheck that crashed, was stopped or got lost
        does not replace a verdict it never gave."""
        d["check"] = self._verdict(path, d.get("check"))
        d["held"] = next((c for c in d.get("checks_done") or ()
                          if self._verdict(path, c)["verdict"] in ("PASS", "FAIL")), None)
        return d

    @staticmethod
    def _proofs(d: dict) -> list[tuple[str, str]]:
        """(blocker, digest) for each content digest the evidence in force was taken at: the newest
        work run's verify and the held verdict's check, unless work came after that check. The
        merge gate compares every one with the worktree by itself; they need not agree with each
        other (a checker may leave files behind that the verify never saw). A digest git could not
        take is "" (see _receipt_digest): it stays in the list, because it cannot vouch for the
        content either; a receipt with no digest field is left out."""
        dig, held = d.get("digests") or {}, d.get("held") or {}
        out = [(CHANGED_VERIFY, dig.get("verify"))]
        if held and not held.get("stale"):
            out.append((CHANGED_CHECK, (dig.get("checks") or {}).get(held.get("n"))))
        return [(why, digest) for why, digest in out if digest is not None]

    @staticmethod
    def _watched(task: dict, d: dict) -> bool:
        """Whether the merge gate compares the worktree's content with the evidence: an open
        worktree thread whose receipts carry a digest (older threads and in-place ones do not)."""
        return (task.get("mode") == "worktree" and d["status"] not in CLOSED
                and bool(TaskStore._proofs(d)))

    def _live_digest(self, task: dict, d: dict) -> tuple[str | None, str | None]:
        """(digest, gap): the worktree's content digest now, for the merge gate to compare with the
        evidence's, and why it could not be read. (None, None) when nothing is compared (see
        _watched), the work is mid-run, or the worktree is unsafe (named by its own blocker); a
        `gap` ("the worktree folder is gone", "git could not read the worktree") when the evidence
        holds a digest and the content cannot be set against it."""
        if (not self._watched(task, d) or d["status"] in ("running", "checking", "stopping")
                or not any(proof for _, proof in self._proofs(d))):
            return None, None
        wt = task.get("worktree") or ""
        if not os.path.isdir(wt):
            return None, "the worktree folder is gone"
        if _worktree_unsafe(wt, task.get("repo") or ""):
            return None, None
        now = _work_digest(wt, self.merge_skip)
        return (now, None) if now else (None, "git could not read the worktree")

    @staticmethod
    def _evidence(d: dict) -> str:
        """How far the work was proved: "verified" (the newest run's verify line passed), "checked"
        (a check run ended in a fresh PASS), both, or "none". A passing verify is the thread's own
        oracle, not proof of correctness; only a check is a second opinion."""
        v = d.get("verify") or {}
        c = (d.get("held") if "held" in d else d.get("check")) or {}      # the verdict in force
        verified = v.get("rc") == 0 and not v.get("error")
        checked = c.get("verdict") == "PASS" and not c.get("stale")
        return ("verified and checked" if verified and checked else "verified" if verified
                else "checked" if checked else "none")

    @staticmethod
    def _merge_blockers(d: dict, now: str | None = None, requires_check: bool = False,
                        gap: str | None = None) -> list[str]:
        """What the evidence says against landing the thread (its own verify, a fresh FAIL verdict,
        a failed, stopped or lost run, a configured verify that never ran, work edited since either was taken, and
        with `requires_check` no fresh PASS from a check run); [] when nothing does. `d` is
        _derive's output with the verdicts read; `now` is the worktree's content digest and `gap`
        why there is none although the evidence holds one (_live_digest): work that cannot be set
        against its evidence is not covered by it."""
        out = []
        v, s = d.get("verify") or {}, d.get("setup") or {}
        c = (d.get("held") if "held" in d else d.get("check")) or {}
        never = d.get("verify_set") and v.get("rc") is None      # stopped, lost or unjudged work
        if v.get("error"):
            out.append(f"verify could not run (exit {v.get('rc')})")
        elif v.get("timed_out") and v.get("rc") not in (None, 0):
            out.append(f"verify timed out after {secs_text(v.get('limit'))} s")
        elif v.get("rc") not in (None, 0):
            out.append(f"verify failed (exit {v['rc']})")
        if c.get("verdict") == "FAIL" and not c.get("stale"):
            out.append(f"check by {c.get('runner') or '?'}: FAIL")
        if d.get("status") == "failed" and v.get("rc") in (None, 0):
            if d.get("setup_failed"):                            # the newest run's own setup
                out.append(f"setup timed out after {secs_text(s.get('limit'))} s" if s.get("timed_out")
                           else f"setup failed (exit {s.get('rc')})")
            elif never and d.get("rc") == 0:
                out.append("verify never ran")
            elif d.get("killed"):
                out.append("the agent's run was killed at its time cap" + cap_text(d.get("cap")))
            else:
                out.append(f"the agent's run failed (exit {d.get('rc')})")
        elif never and d.get("status") in ("stopped", "lost", "review"):
            out.append("verify never ran")
        elif d.get("status") == "stopped":           # whatever the verify (or none) says
            out.append("the agent's run was stopped before it finished")
        elif d.get("status") == "lost":
            out.append("the agent's run was lost (its supervisor died); nothing says it finished")
        proofs = TaskStore._proofs(d)
        for why, proof in proofs:
            if not proof:
                out.append(UNPROVEN[why])
            elif now and proof != now:
                out.append(why)
        if gap and any(proof for _, proof in proofs):
            out.append(f"the work cannot be compared with its evidence: {gap}")
        # a fresh FAIL is named above; a missing, outdated, crashed or unfinished check is no PASS
        fresh = c.get("verdict") in ("PASS", "FAIL") and not c.get("stale")
        if requires_check and not fresh:
            out.append("no independent check")
        return out

    @staticmethod
    def _tail(file: str, size: int) -> str | None:
        try:
            with open(file, "rb") as handle:
                handle.seek(max(0, os.path.getsize(file) - size))
                return handle.read().decode("utf-8", "replace")
        except OSError:
            return None

    _SUMMARY_CACHE: dict = {}
    _SUMMARY_LOCK = threading.Lock()         # the cache is class-wide: every store shares it

    def summary(self, path: str) -> dict:
        out = self._summary(path)
        if out.get("mode") != "worktree" or out.get("status") in CLOSED or not out.get("repo"):
            return out
        try:                                   # what the files cannot show: the agent changed its worktree's .git
            wt = self._read_task(path).get("worktree") or ""
        except (OSError, ValueError):
            return out
        why = _worktree_unsafe(wt, out["repo"]) if os.path.isdir(wt) else None
        if why:
            out = dict(out, merge_blockers=[_unsafe_text(why), *out.get("merge_blockers", [])])
        return out

    def _summary(self, path: str) -> dict:
        try:
            # size as well as mtime: an append inside one timestamp tick still changes the key
            files = (os.path.join(path, "task.json"), os.path.join(path, "events.jsonl"))
            key = tuple((st.st_mtime_ns, st.st_size) for st in map(os.stat, files))
            key += (self.merge_requires_check,)       # the blockers depend on it
        except OSError:
            key = None
        with self._SUMMARY_LOCK:
            hit = self._SUMMARY_CACHE.get(path) if key else None
        if hit and hit[0] == key:
            return dict(hit[1])
        task = self._read_task(path)
        d = self._read_verdicts(path, self._derive(task, self._events(path)))
        out = {"id": task["id"], "title": task.get("title", ""), "text": task.get("text", "")[:600],
                "runner": task.get("runner"), "model": task.get("model"), "target": task.get("target"), "cwd": task.get("cwd"),
                "mode": task.get("mode"), "branch": task.get("branch"), "worktree": task.get("worktree"),
                "base_branch": task.get("base_branch"),
                "repo": task.get("repo"), "created": task.get("created"), "verify_cmd": task.get("verify"), "retries": int(task.get("retries") or 0),
                "setup_cmd": task.get("setup"), "note": task.get("note") or "", "origin": task.get("origin"),
                **{k: d[k] for k in ("status", "rc", "runs", "started", "ended", "check", "verify", "attempts", "setup")}}
        out["retry_due"] = d.get("retry_due", False)
        out["blocked"] = d.get("blocked")         # why a due retry can never go out (None = it can)
        held = d.get("held")
        out["last_verdict"] = {k: held.get(k) for k in ("n", "runner", "model", "verdict", "at",
                                                        "stale")} if held else None
        now, gap = self._live_digest(task, d)
        out["merge_blockers"] = self._merge_blockers(d, now, self.merge_requires_check, gap=gap)
        out["evidence"] = self._evidence(d)
        # the page is written as UTF-8: a record whose text cannot be (a lone surrogate that an
        # older map accepted) is an unreadable record, listed as such, never a crash of the page
        json.dumps(out, ensure_ascii=False).encode("utf-8")
        # settled: the files decide ("new" is only ever the moment before the first run event);
        # except while a blocker depends on the worktree itself, which the files do not show
        if (key and out["status"] not in ("new", "running", "checking", "stopping", "lost")
                and not self._watched(task, d)):
            with self._SUMMARY_LOCK:
                self._SUMMARY_CACHE[path] = (key, dict(out))
        return out

    def _rows(self):
        """Every thread's summary, newest first (an unreadable record as an "error" row)."""
        try:
            names = sorted((n for n in os.listdir(self.tasks_dir) if TASK_ID_RE.fullmatch(n)), reverse=True)
        except OSError:
            return
        for name in names:
            try:
                yield self.summary(os.path.join(self.tasks_dir, name))
            except (OSError, ValueError, KeyError, TypeError):
                yield {"id": name, "title": "(unreadable task record)", "status": "error"}

    def list(self) -> list[dict]:
        """Every open thread, and the newest MAX_TASKS_LISTED closed ones (history), newest first.
        The limit is for history only: an old thread still open is never cut off."""
        out, history = [], 0
        for row in self._rows():
            if row.get("status") in CLOSED or row.get("status") == "error":
                history += 1
                if history > MAX_TASKS_LISTED:
                    continue
            out.append(row)
        return out

    def find_by_origin(self, origin: object) -> list[str]:
        """The ids of the threads made with exactly this origin, oldest first: their task.json, or the
        preparing.json a creator that died left behind (a workflow re-binds a launch it lost this way)."""
        if not isinstance(origin, dict) or not origin:
            return []
        try:
            names = sorted(n for n in os.listdir(self.tasks_dir) if TASK_ID_RE.fullmatch(n))
        except OSError:
            return []
        hits = []
        for name in names:
            for file in ("task.json", "preparing.json", "prepared.json"):
                try:
                    with open(os.path.join(self.tasks_dir, name, file), encoding="utf-8") as handle:
                        rec = json.load(handle)
                except (OSError, ValueError):
                    continue
                task = rec if file == "task.json" else (rec.get("task") if isinstance(rec, dict) else None)
                if isinstance(task, dict) and task.get("origin") == origin:
                    hits.append(name)
                    break
        return hits

    def get(self, tid: object, run: object = None, since: object = 0) -> dict:
        path = self._dir(tid)
        info = self.summary(path)
        events = self._events(path)
        n = info["runs"] if run in (None, "", 0) else run
        if not isinstance(n, int) or n < 0 or n > max(1, info["runs"]):
            raise TaskError(400, "run must name a run of this task")
        if not isinstance(since, int) or since < 0:
            raise TaskError(400, "since must be a byte offset")
        chunk, offset, size = "", since, 0
        log = os.path.join(path, f"run-{n}.log")
        try:
            size = os.path.getsize(log)
            start = min(since, size)
            with open(log, "rb") as handle:
                handle.seek(start)
                raw = handle.read(LOG_CHUNK)
            ended = info["status"] not in ("running", "checking", "stopping", "lost")
            check = info.get("check") or {}
            if info["status"] == "lost" or (check.get("n") == n and check.get("verdict") == "lost"):
                # the supervisor is gone; is anything it started still writing?
                ended = not any(_owned_group(g, info.get("cwd") or "")
                                for g in self._run_groups(events, n))
            over = ended and start + len(raw) >= size   # the run is over: take its tail too
            cut = raw.rfind(b"\n")                        # else whole lines only; the rest arrives next poll
            take = len(raw) if (over or (cut < 0 and len(raw) >= LOG_CHUNK)) else cut + 1
            chunk = raw[:take].decode("utf-8", "replace")
            offset = start + take
        except OSError:
            pass
        verify_tail = self._tail(os.path.join(path, f"verify-{n}.log"), 2048)
        setup_tail = self._tail(os.path.join(path, f"setup-{n}.log"), 2048)
        lines = []
        for line in chunk.splitlines():
            lines.extend(parse_line(line))
        prompts = []
        for e in events:
            if e["ev"] == "run":
                prompts.append({"n": e.get("n"), "text": str(e.get("text", ""))[:MAX_TEXT], "at": e.get("at"),
                                "runner": e.get("runner"), "model": e.get("model"), "kind": e.get("kind") or "task",
                                "followup": bool(e.get("followup")), "auto": bool(e.get("auto"))})
        exits = {e.get("n"): e for e in events if e["ev"] == "exit"}
        for p in prompts:
            ex = exits.get(p["n"])
            p["rc"] = ex.get("rc") if ex else None
            p["ended"] = ex.get("at") if ex else None
        return info | {"text": self._read_task(path).get("text", ""), "run": n, "offset": offset, "size": size,
                       "more": offset < size, "lines": lines, "prompts": prompts, "verify_tail": verify_tail,
                       "setup_tail": setup_tail,
                       "closed_note": next((e for e in reversed(events) if e["ev"] in CLOSED), None)}

    # ---- creating and running ------------------------------------------------------------------
    def _check_target(self, target: object) -> str:
        if (not isinstance(target, str) or not target.startswith("/") or "\x00" in target
                or len(target) > 1000 or _unencodable(target)):
            raise TaskError(400, "target must be an absolute folder path")
        real = os.path.realpath(target)
        if os.path.isfile(real):
            real = os.path.dirname(real)
        if not os.path.isdir(real):
            raise TaskError(404, "no such folder")
        if not any(real == r or real.startswith(r.rstrip("/") + "/") for r in self.roots):
            raise TaskError(403, "target is outside the roots this map shows")
        if any(real == d or real.startswith(d + "/") for d in (self.tasks_dir, self.worktrees_dir)):
            raise TaskError(403, "a task cannot run inside the task store")
        return real

    @staticmethod
    def _check_text(text: object) -> str:
        if not isinstance(text, str) or not text.strip():
            raise TaskError(400, "say what should be done")
        if len(text) > MAX_TEXT or any(ord(c) < 32 and c not in "\n\t" for c in text):
            raise TaskError(400, f"text must be plain text under {MAX_TEXT} characters")
        if _unencodable(text):
            raise TaskError(400, "text must be valid text: it holds a character that cannot be "
                                 "written as UTF-8 (a lone surrogate, or a byte that is not UTF-8)")
        return text.strip()

    def running_count(self) -> int:
        """The places taken: runs going (running, checking, stopping) of every thread, and lost
        threads whose agent (or setup, verify) still has a live process group in the folder,
        until they are stopped."""
        taken = 0
        for row in self._rows():
            status = row.get("status")
            if status in ("running", "checking", "stopping"):
                taken += 1
            elif status not in CLOSED and status != "error" and self._lost_but_alive(row):
                taken += 1
        return taken

    def _lost_but_alive(self, row: dict) -> bool:
        if row.get("status") != "lost" and (row.get("check") or {}).get("verdict") != "lost":
            return False
        path = os.path.join(self.tasks_dir, row["id"])
        try:
            return bool(self._orphans(self._read_task(path), self._events(path)))
        except (OSError, ValueError, KeyError, TypeError):
            return False

    @contextlib.contextmanager
    def _capacity(self):
        """The store-wide lock for the decision to start a run: held from the check for room until
        the run is on record, by whichever process (the CLI, the bridge) is starting one, so two
        of them cannot both see the last free place."""
        self._make_store_dir()
        with _lock_file(os.path.join(self.tasks_dir, ".store.lock"), LOCK_WAIT, "the thread store"):
            yield

    def _make_store_dir(self) -> None:
        """tasks_dir, made if need be. The store's folder is the map's own: when this makes the
        .container-map folder around it, the repo it sits in stops listing it as a change. It runs
        before the first lock is taken (the lock file would make the folder first)."""
        import config
        config.make_state_dir(self.tasks_dir, 0o700)

    def _has_room(self) -> bool:
        return self.running_count() < self.max_running

    def _repo_lock_path(self, repo: str) -> str:
        """The lock file of a repository: in its git common dir (shared by every worktree of it),
        else, where that cannot be found or written, one named for the repo in tasks_dir."""
        common = _git(["rev-parse", "--git-common-dir"], repo, timeout=10)
        line = common.stdout.strip() if common.returncode == 0 else ""
        if line:
            base = os.path.realpath(line if os.path.isabs(line) else os.path.join(repo, line))
            if os.path.isdir(base) and os.access(base, os.W_OK):
                return os.path.join(base, "container-map.lock")
        self._make_store_dir()
        digest = hashlib.sha256(os.path.realpath(repo).encode("utf-8")).hexdigest()[:16]
        return os.path.join(self.tasks_dir, f".repo-{digest}.lock")

    @contextlib.contextmanager
    def _repo_lock(self, repo: str | None):
        """Held across a whole mutation of the repository (merge, discard, worktree add), against
        every other store and process that changes it. No repo: nothing to hold."""
        if not repo:
            yield
            return
        with _lock_file(self._repo_lock_path(repo), LOCK_WAIT, "this repository", hold=True):
            yield

    def _check_model(self, runner: str, model: object):
        spec = self.runners[runner]
        if model in (None, ""):
            return spec.get("default_model")
        if not isinstance(model, str) or not any(m["name"] == model for m in spec.get("models", [])):
            raise TaskError(400, f"pick one of the models {runner} offers" if spec.get("models") else f"{runner} offers no model choice")
        return model

    @staticmethod
    def _check_verify(verify: object, retries: object) -> tuple:
        verify = _check_line(verify, "verify")
        if retries in (None, ""):
            retries = 0
        if not isinstance(retries, int) or isinstance(retries, bool) or not 0 <= retries <= MAX_RETRIES:
            raise TaskError(400, f"retries must be a whole number from 0 to {MAX_RETRIES}")
        if retries and not verify:
            raise TaskError(400, "retries need a verify command to retry against")
        return verify, retries

    def create(self, text: object, target: object, runner: object, isolate: object = True, model: object = None,
               verify: object = None, retries: object = 0, setup: object = None,
               worktree_only: bool = False, title: object = None, origin: object = None,
               env: object = None) -> dict:
        """worktree_only: refuse, with the reason, a folder the thread could only work in place in
        (not in a git repo, a repo above the map's roots, a repo with no commit). A phone starts
        threads this way: it never gets to work in the folder itself.
        title: what names the thread (default: the text's first line). origin: who asked, kept in
        task.json (a workflow step: {workflow, step, attempt}; find_by_origin finds it again after a
        crash). env: CM_WORKFLOW* variables for the first run only, never written down."""
        text = self._check_text(text)
        title = self._check_title(title)
        origin = self._check_origin(origin)
        env = self._check_env(env)
        verify, retries = self._check_verify(verify, retries)
        setup = _check_line(setup, "setup")
        if not isinstance(runner, str) or runner not in self.runners:
            raise TaskError(400, "pick one of the offered agents" if self.runners else "no agents are configured or installed")
        if self.runners[runner].get("role") == "check":
            raise TaskError(400, f"{runner} only checks work; pick an agent that does it")
        if not isinstance(isolate, bool):
            raise TaskError(400, "isolate must be true or false")
        model = self._check_model(runner, model)
        real = self._check_target(target)
        with self._gate:
            now = time.monotonic()
            if now - self.last_create < CREATE_MIN_GAP:
                raise TaskError(429, "one new task every couple of seconds")
            with self._capacity():          # until the first run is on record
                if not self._has_room():
                    raise TaskError(429, f"{self.max_running} tasks are already running; "
                                         "wait for one to finish")
                self.last_create = now
                return self._create_task(text, real, runner, isolate, model, verify, retries, setup,
                                         worktree_only, title=title, origin=origin, env=env)

    @staticmethod
    def _check_title(title: object) -> str | None:
        if title in (None, ""):
            return None
        if (not isinstance(title, str) or not title.strip() or len(title.strip()) > 90
                or any(ord(c) < 32 or ord(c) == 127 for c in title) or _unencodable(title)):
            raise TaskError(400, "title must be one line of plain text, 90 characters at most")
        return title.strip()

    @staticmethod
    def _check_origin(origin: object) -> dict | None:
        if origin is None:
            return None
        try:
            size = len(json.dumps(origin, sort_keys=True).encode("utf-8"))
        except (TypeError, ValueError):
            size = ORIGIN_MAX + 1
        if not isinstance(origin, dict) or not origin or size > ORIGIN_MAX or not all(isinstance(k, str) for k in origin):
            raise TaskError(400, "origin must be a small object (who asked for this thread)")
        return dict(origin)

    @staticmethod
    def _check_env(env: object) -> dict:
        if env in (None, {}):
            return {}
        if (not isinstance(env, dict) or len(env) > 8
                or not all(isinstance(k, str) and RUN_ENV_RE.fullmatch(k) and isinstance(v, str)
                           and "\x00" not in v and len(v) <= 400 for k, v in env.items())):
            raise TaskError(400, "env may only carry CM_WORKFLOW variables")
        return dict(env)

    def _create_task(self, text: str, real: str, runner: str, isolate: bool, model, verify,
                     retries: int, setup: str | None = None, worktree_only: bool = False,
                     title: str | None = None, origin: dict | None = None, env: dict | None = None) -> dict:
        tid = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.now())) + "-" + _slug(text) + "-" + secrets.token_hex(3)
        path = os.path.join(self.tasks_dir, tid)
        self._make_store_dir()
        os.makedirs(path, mode=0o700)
        task = {"version": 1, "id": tid, "title": title or text.splitlines()[0][:90], "text": text, "runner": runner, "model": model,
                "verify": verify, "retries": retries, "setup": None,
                "target": real, "cwd": real, "mode": "inplace", "repo": None, "base_sha": None, "base_branch": None,
                "branch": None, "worktree": None, "dirty_before": [], "created": stamp(self.now()), "note": "",
                "config": self.config_path}
        if origin:
            task["origin"] = origin
        try:
            # Nobody can hold this lock before task.json exists, and it is let go only once the
            # first run is on record: an early reply, stop or close waits for it.
            with _locked(path):
                self._note_preparing(path, task)       # what a crash from here on is recovered by
                self._prepare_repo(path, task, real, isolate, setup, worktree_only)
                if task["mode"] != "worktree":
                    self._plan_setup(task, setup)
                self._write_json(os.path.join(path, "task.json"), task, indent=1)
                self._start_run(path, task, 1, text, followup=False, extra_env=env)
                self._finish_preparing(path, "ready")
        except TaskError:
            if not os.path.exists(os.path.join(path, "task.json")):
                self._abandon(path)                    # a refused worktree: no thread exists
            else:                                      # the agent would not start: a failed run
                self._finish_preparing(path, "ready")
            raise
        except Exception:
            self._reconcile_dir(path)                  # lock free: what was made becomes a thread
            raise
        return self.summary(path)

    def _plan_setup(self, task: dict, setup: str | None) -> None:
        """A worktree thread's setup line: its own, else the config's for the repo, else "*"."""
        if task["mode"] == "worktree":
            task["setup"] = setup or self.worktree_setup.get(task["repo"]) or self.worktree_setup.get("*")
        elif setup:
            task["note"] = ((task["note"] + "; " if task["note"] else "") +
                            "setup runs only in a thread's own worktree; this one works in place, so it was skipped")

    @staticmethod
    def _write_json(file: str, obj: dict, indent: int | None = None) -> None:
        """Whole or not at all: a crash mid-write leaves the old file (or none), never half."""
        tmp = file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(obj, handle, indent=indent)
        os.replace(tmp, file)

    def _note_preparing(self, path: str, task: dict) -> None:
        """preparing.json: the thread as planned, on record before anything outside its folder is
        made (the worktree and branch). If the creating process dies, reconcile() finishes it."""
        self._write_json(os.path.join(path, "preparing.json"),
                         {"state": "preparing", "task": task, "at": stamp()})

    def _finish_preparing(self, path: str, state: str, reason: str | None = None) -> None:
        """preparing.json becomes prepared.json, saying how it ended (ready, failed, abandoned)."""
        marker = os.path.join(path, "preparing.json")
        try:
            with open(marker, encoding="utf-8") as handle:
                rec = json.load(handle)
        except (OSError, ValueError):
            return
        if isinstance(rec, dict):
            rec.update(state=state, closed=stamp())
            if reason:
                rec["reason"] = reason
            self._write_json(marker, rec)
            os.replace(marker, os.path.join(path, "prepared.json"))

    @staticmethod
    def _abandon(path: str) -> None:
        """A thread that never got a record: its own just-made files and folder go (an empty
        folder; nothing of the task or the user's exists in it)."""
        for name in (".lock", "preparing.json", "preparing.json.tmp"):
            with contextlib.suppress(OSError):
                os.unlink(os.path.join(path, name))
        with contextlib.suppress(OSError):
            os.rmdir(path)

    def reconcile(self) -> list[tuple[str, str]]:
        """Finish every preparation a dead creator left open (the CLI killed while it made a
        worktree). Runs when the store opens and on every tick. [(id, outcome)] of those done:
        "failed" (a worktree or branch was made: a thread of its own that you can reply to, merge
        or set aside), "abandoned" (nothing was made: closed as done), "ready" (it had finished).
        A creator still at work holds the thread's lock and is left alone."""
        done = []
        try:
            names = sorted(n for n in os.listdir(self.tasks_dir) if TASK_ID_RE.fullmatch(n))
        except OSError:
            return done
        for name in names:
            try:
                outcome = self._reconcile_dir(os.path.join(self.tasks_dir, name))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.reconcile_errors.append((name, f"{type(exc).__name__}: {exc}"[:300]))
                continue
            if outcome:
                done.append((name, outcome))
        return done

    def _reconcile_dir(self, path: str) -> str | None:
        marker = os.path.join(path, "preparing.json")
        if not os.path.exists(marker):
            return None
        try:
            with _locked(path, wait=0):
                return self._reconcile_locked(path, marker)
        except LockBusy:
            return None

    def _reconcile_locked(self, path: str, marker: str) -> str | None:
        try:
            with open(marker, encoding="utf-8") as handle:
                rec = json.load(handle)
        except (OSError, ValueError):
            return None
        task = rec.get("task") if isinstance(rec, dict) else None
        if (not isinstance(task, dict) or rec.get("state") != "preparing"
                or task.get("id") != os.path.basename(path)):
            return None
        record = os.path.join(path, "task.json")
        if os.path.isfile(record) and any(e["ev"] == "run" for e in self._events(path)):
            self._finish_preparing(path, "ready")        # it had finished; only the marker was left
            return "ready"
        if os.path.isfile(record):
            with contextlib.suppress(OSError, ValueError):
                task = self._read_task(path)                # the fuller record, if it is whole
        wt, repo, branch = task.get("worktree"), task.get("repo"), task.get("branch")
        has_wt = task.get("mode") == "worktree" and bool(wt) and os.path.isdir(wt)
        has_branch = False
        if task.get("mode") == "worktree" and repo and branch:
            ref = _git(["rev-parse", "-q", "--verify", f"refs/heads/{branch}"], repo)
            has_branch = ref.returncode == 0
        made = has_wt or has_branch
        if has_wt and not os.path.isdir(task.get("cwd") or ""):
            task["cwd"] = wt
        said = ("creation was interrupted before the agent started; the worktree and branch were "
                "kept: reply to start the agent, or set it aside" if made else
                "creation was interrupted: nothing was created")
        task["note"] = (task["note"] + "; " if task.get("note") else "") + said
        self._write_json(record, task, indent=1)
        self._append(path, {"ev": "run", "n": 1, "pid": 0, "runner": task.get("runner"),
                            "model": task.get("model"), "kind": "task",
                            "text": str(task.get("text", "")), "followup": False, "auto": False,
                            "cap": 0, "pin": _pin(task), "at": stamp()})
        self._append(path, {"ev": "exit", "n": 1, "rc": 127, "killed": False, "stopped": False,
                            "at": stamp(), "error": said[:200]})
        if not made:
            self._append(path, {"ev": "done", "note": said, "at": stamp()})
        outcome = "failed" if made else "abandoned"
        self._finish_preparing(path, outcome, said)
        return outcome

    @staticmethod
    def _needs_worktree(why: str) -> TaskError:
        return TaskError(400, "a thread started from a phone works in its own worktree, which needs "
                              f"a folder inside a git repository with at least one commit: {why}. "
                              "Start it from the desktop page to work in place")

    def _prepare_repo(self, path: str, task: dict, real: str, isolate: bool,
                      setup: str | None = None, worktree_only: bool = False) -> None:
        """The git half of create: find the repo, give the task its worktree or note dirty files.
        The repository is locked for it (see _repo_lock). worktree_only turns every way of ending
        up in place into a refusal (nothing exists yet: create() abandons the folder)."""
        top = _git(["rev-parse", "--show-toplevel"], real, timeout=10)
        repo = top.stdout.strip() if top.returncode == 0 and top.stdout.strip() else None
        why = f"{real} is not inside a git repository"
        if repo:
            repo = os.path.realpath(repo)
            if not any(repo == r or repo.startswith(r.rstrip("/") + "/") for r in self.roots):
                task["note"] = "the folder's git repo sits above the map's roots, so the thread works in place"
                why = "its git repository sits above the folders this map shows"
                repo = None
        if not repo:
            if worktree_only:
                raise self._needs_worktree(why)
            return
        with self._repo_lock(repo):
            head = _git(["rev-parse", "--verify", "-q", "HEAD"], repo, timeout=10)
            task["repo"] = repo
            task["base_sha"] = head.stdout.strip() if head.returncode == 0 else None
            br = _git(["symbolic-ref", "--short", "-q", "HEAD"], repo, timeout=10)
            task["base_branch"] = br.stdout.strip() if br.returncode == 0 and br.stdout.strip() else None
            if worktree_only and not (isolate and task["base_sha"]):
                raise self._needs_worktree(
                    f"the repository at {repo} has no commits yet" if isolate
                    else "the thread was not asked to use a worktree")
            if isolate and task["base_sha"]:
                branch = "cm/" + task["id"]
                wt = self._worktree_path(task["id"], path)
                try:
                    os.makedirs(os.path.dirname(wt), mode=0o700, exist_ok=True)
                except OSError as exc:      # no thread exists yet: create() abandons the folder
                    raise TaskError(
                        502, f"could not make the worktrees folder ({os.path.dirname(wt)}): {exc}. "
                             "Set worktrees_dir to a folder you can write") from exc
                sub = os.path.relpath(real, repo)
                task.update(mode="worktree", worktree=wt, branch=branch,
                            cwd=os.path.normpath(os.path.join(wt, sub)) if sub != "." else wt)
                self._plan_setup(task, setup)
                self._note_preparing(path, task)                # before git makes anything
                fresh = (not os.path.lexists(wt), not self._has_branch(repo, branch))
                made = _git(["worktree", "add", "-q", "-b", branch, wt, task["base_sha"]], repo, hooks=True)
                if made.returncode != 0:
                    left = self._undo_worktree_add(repo, wt, branch, *fresh)
                    raise TaskError(502, "git worktree add failed: "
                                    + _first_lines(made.stderr or made.stdout)
                                    + (f". It left {' and '.join(left)}, which could not be taken "
                                       "away; remove them by hand" if left else ""))
                if not os.path.isdir(task["cwd"]):              # an untracked folder does not exist in a fresh checkout
                    task["cwd"] = wt
                    task["note"] = f"{sub} has no committed files, so the task starts at the repo root"
            else:
                if isolate and not task["base_sha"]:
                    task["note"] = "the repo has no commits yet, so the task works in place"
                st = _git(["status", "--porcelain=v1", "-z", "-uall"], repo, timeout=20)
                task["dirty_before"] = _porcelain_paths(st.stdout)[:2000] if st.returncode == 0 else []
                try:
                    task["saved_before"] = self._presave(path, repo, task["dirty_before"])
                except OSError as exc:
                    task["saved_before"] = []
                    task["note"] = (task["note"] + "; " if task["note"] else "") + f"could not copy your uncommitted files aside: {exc}"

    @staticmethod
    def _has_branch(repo: str, branch: str) -> bool:
        return _git(["rev-parse", "-q", "--verify", f"refs/heads/{branch}"], repo,
                    timeout=10).returncode == 0

    @staticmethod
    def _undo_worktree_add(repo: str, wt: str, branch: str, fresh_wt: bool,
                           fresh_branch: bool) -> list[str]:
        """A `git worktree add -b` that failed (a checkout filter that errors, a timeout) may have
        made the branch and part of the folder before it did. No thread will ever own them, and
        create() is about to forget the preparation record that names them, so what this call
        made is taken away here: the folder (only if it did not exist before) through git, else
        moved aside, never deleted by hand; the branch (only if it did not exist before, and it
        holds nothing: it is the base commit). Returns what could not be taken away."""
        left = []
        if fresh_wt and os.path.lexists(wt):
            _git(["worktree", "remove", "--force", "--force", wt], repo)    # twice: a cut-short checkout is locked
            if os.path.lexists(wt):
                aside = wt + ".retired-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime())
                try:
                    os.rename(wt, aside)
                except OSError:
                    left.append(f"the folder {wt}")
        _git(["worktree", "prune"], repo)
        if fresh_branch and TaskStore._has_branch(repo, branch):
            if _git(["branch", "-D", branch], repo).returncode != 0:
                left.append(f"the branch {branch}")
        return left

    def _worktree_path(self, tid: str, path: str) -> str:
        """Where a new thread's worktree goes: beside the receipts folders, never inside `path`,
        so an agent's ../task.json and ../events.jsonl are not the thread's receipts."""
        wts, tasks = self.worktrees_dir, self.tasks_dir
        if wts == tasks or wts.startswith(tasks + "/"):
            raise TaskError(500, f"worktrees_dir ({wts}) is inside tasks_dir; an agent could "
                                 "reach the receipts from its worktree. Point it elsewhere")
        return os.path.join(self.worktrees_dir, tid)

    @staticmethod
    def _presave(path: str, repo: str, rel_paths: list) -> list[str]:
        """Working in place in a repo with uncommitted changes: copy those files aside first, so an
        agent that rewrites them cannot cost you your edits (restore them from <task>/before/)."""
        saved, total, base = [], 0, os.path.join(path, "before")
        for rel in rel_paths[:PRESAVE_MAX_FILES]:
            src = os.path.join(repo, rel)
            if not os.path.isfile(src) or os.path.islink(src):
                continue
            size = os.path.getsize(src)
            if total + size > PRESAVE_MAX_BYTES:
                break
            dst = os.path.normpath(os.path.join(base, rel))
            if not dst.startswith(base + "/"):
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            saved.append(rel)
            total += size
        return saved

    def _session_of(self, path: str, runner: str) -> str | None:
        """The maker's newest session id: the first id line of its latest non-check run's log."""
        try:
            events = self._events(path)
        except (OSError, ValueError):
            return None
        work = [e["n"] for e in events if e["ev"] == "run" and e.get("runner") == runner and e.get("kind", "task") != "check" and isinstance(e.get("n"), int)]
        for n in sorted(work, reverse=True):
            try:
                with open(os.path.join(path, f"run-{n}.log"), "r", encoding="utf-8", errors="replace") as handle:
                    for _ in range(40):
                        line = handle.readline()
                        if not line:
                            break
                        m = SESSION_RE.search(line)
                        if m:
                            return m.group(1)
            except OSError:
                continue
        return None

    def _check_prompt(self, task: dict, text: str) -> str:
        # a reviewer has a short cap (check_minutes): it reads, it does not rerun the maker's whole job,
        # or the cap kills it before it writes a verdict
        base, verify = task.get("base_sha"), task.get("verify")
        how = (f"Read the task and the diff: `git diff {base[:12]}` (and `git status`) shows everything it changed."
               if base and task.get("repo") else "Read the task and the changed files; there is no git history here.")
        limit = "do not run the whole test suite or any long command."
        run = (f"Run the thread's verify command once, and nothing heavier:\n\n    {verify}\n\nOther than that, {limit}"
               if verify else limit[0].upper() + limit[1:])
        return (f"CHECK, not a task. Another agent was asked to do this in {task['cwd']}:\n\n{task.get('text', '')}\n\n"
                f"{('The person adds: ' + text) if text else ''}\n\n---\n{how} {run} "
                "Do NOT modify, create or delete any file and do not commit: you are the reviewer, not the "
                "maker. Judge whether the work does what was asked, is complete, and breaks nothing you can see. End within a "
                "few minutes with exactly one final line: VERDICT: PASS or VERDICT: FAIL, followed by one sentence of reason.")

    def _prompt(self, task: dict, text: str, n: int, prev_rc, prev_setup=None,
                prev_status: str | None = None, prev_setup_limit=None, prev_cap=None) -> str:
        if n == 1:
            where = f"You are working in {task['cwd']}."
            if task["mode"] == "worktree":
                where += (f" It is a git worktree on branch {task['branch']}, made just for this task from the last commit;"
                          " your changes are reviewed as a diff before anyone merges them, so edit freely and commit if you like.")
            rules = " If you commit, do not add Co-Authored-By or any other AI attribution trailer." if self.no_ai_trailers else ""
            return f"{text}\n\n---\n{where}{rules} When you finish, end with a short summary of what you changed and anything left undone."
        if prev_setup is not None:
            how = (f"timed out after {secs_text(prev_setup_limit)} s" if prev_setup_limit
                   else f"failed (exit {prev_setup})")
            before = (f"Your previous run never started: the worktree's setup line {how}, so it "
                      "runs again before you do.")
        elif prev_cap is not None:
            before = ("Your previous run hit its time cap" + cap_text(prev_cap) + " and was killed "
                      "before it finished. Look at what it left in this folder before you go on, "
                      "and work in steps small enough to finish inside the cap.")
        elif prev_status == "lost":
            before = ("Your previous run was lost: the process that supervised it died, so there "
                      "is no exit code and nothing says whether it finished. Look at what it "
                      "left in this folder before you go on.")
        elif prev_status == "stopped" and prev_rc is None:
            before = "Your previous run was stopped before it finished."
        elif prev_rc is None:
            before = "Your previous run left no exit code."
        else:
            before = f"Your previous run ended with exit code {prev_rc}."
        return (f"{text}\n\n---\nThis is a follow-up in the same folder ({task['cwd']}). The original task was:\n"
                f"{task.get('text', '')}\n{before}")

    def _start_run(self, path: str, task: dict, n: int, text: str, followup: bool, prev_rc=None,
                   runner: str | None = None, model: str | None = None, kind: str = "task",
                   auto: bool = False, prev_setup=None, prev_status: str | None = None,
                   prev_setup_limit=None, prev_cap=None, extra_env: dict | None = None) -> None:
        runner = runner or task["runner"]
        spec = self.runners[runner]
        prompt = (self._check_prompt(task, text) if kind == "check" else
                  self._prompt(task, text, n, prev_rc, prev_setup, prev_status, prev_setup_limit,
                               prev_cap))
        if kind == "check":
            argv = list(spec.get("check_cmd") or spec["cmd"])
        elif followup and spec.get("followup_cmd"):
            argv = list(spec["followup_cmd"])
            if any("{session}" in a for a in argv):
                session = self._session_of(path, task["runner"])
                if session:
                    argv = [a.replace("{session}", session) for a in argv]
                else:                                    # nothing to resume: a fresh run carrying the task text
                    argv = list(spec["cmd"])
        else:
            argv = list(spec["cmd"])
        model = model if kind == "check" else task.get("model")
        chosen = next((m for m in spec.get("models", []) if m["name"] == model), None)
        # the supervisor's environment is what its agent, its verify and its setup line inherit:
        # the map's own variables stay behind (see agent_env); the runner's own env is added
        env = agent_env(chosen["env"] if chosen else None)
        env.update(extra_env or {})              # create(env=): a workflow step's CM_WORKFLOW* (first run only)
        if chosen:
            argv = with_model_args(argv, chosen["args"])
        inline = any("{text}" in a for a in argv)
        argv = [a.replace("{text}", prompt) for a in argv]
        with open(os.path.join(path, f"prompt-{n}.txt"), "w", encoding="utf-8") as handle:
            handle.write(prompt)
        minutes = (spec.get("check_minutes") or min(spec["max_minutes"], CHECK_MINUTES)) if kind == "check" else spec["max_minutes"]
        cap = int(minutes * 60)
        # the verify and setup lines travel in the supervisor's argv ("" = none): it never reads
        # them back from task.json, which the agent can reach (../task.json from its worktree)
        verify = task["verify"] if (kind == "task" and task.get("verify")) else ""
        # setup: a worktree thread's work runs until one setup has passed (never a check), so a
        # follow-up after a failed setup runs it again before the agent
        passed = any(e["ev"] == "setup" and e.get("rc") == 0 for e in self._events(path))
        setup = task["setup"] if (kind == "task" and task.get("mode") == "worktree"
                                  and task.get("setup") and not passed) else ""
        scoped = scope_prefix(task["id"], n) if self.scope else []
        # a worktree thread's receipts carry a digest of its content: the verify's, and a check's
        # at its exit ("v" / "c"; "" = none, the work is not in a repo of its own)
        digest = ("c" if kind == "check" else "v") if task.get("mode") == "worktree" else ""
        # the head fields after the setup line: a scope of its own to sweep ("1"), the digest flag,
        # the merge_skip list (JSON) the digest leaves out, and the time caps of the verify and
        # the setup line in seconds
        sup = scoped + [self.python, "-I", TASKS_PY, "--exec", path, str(n), str(cap),
                        "1" if inline else "0", task["id"], verify, setup, "1" if scoped else "0",
                        digest, json.dumps(self.merge_skip), str(self.verify_timeout),
                        str(self.setup_timeout), "--", *argv]
        pin = {"pin": _pin(task)} if n == 1 else {}       # what _trusted holds the record to later
        try:
            proc = subprocess.Popen(sup, cwd=task["cwd"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True, env=env)
        except OSError as exc:
            self._append(path, {"ev": "run", "n": n, "pid": 0, "runner": runner, "model": model, "kind": kind, "text": text, "followup": followup, "auto": auto,
                                "cap": cap, **pin, "at": stamp()})
            self._append(path, {"ev": "exit", "n": n, "rc": 127, "killed": False, "stopped": False, "at": stamp(), "error": str(exc)[:200]})
            raise TaskError(502, f"the agent could not be started: {exc}") from exc
        threading.Thread(target=proc.wait, name=f"reap-{task['id']}-{n}", daemon=True).start()   # no zombies
        self._append(path, {"ev": "run", "n": n, "pid": proc.pid, "runner": runner, "model": model,
                            "kind": kind, "text": text, "followup": followup, "auto": auto,
                            "cap": cap, **pin, "at": stamp()})

    def tick(self) -> list[str]:
        """The oracle loop: a thread whose verify command failed and has retries left is sent back
        to its agent with the failure, as a follow-up, until it passes or the retries are spent.
        Called by the bridge on every list and by its own clock; returns the ids it retried. One
        tick at a time: a caller that finds another running returns at once; that one does it."""
        if not self._tick_lock.acquire(blocking=False):
            return []
        try:
            return self._tick()
        finally:
            self._tick_lock.release()

    def _tick(self) -> list[str]:
        """One pass, oldest thread first. A retry that cannot go out (an altered record, an agent
        that would not start) is reported through on_tick_error and the pass goes on: one broken
        thread never holds back the others or loses the ids already retried."""
        retried, self.tick_errors = [], []
        seen = len(self.reconcile_errors)
        self.reconciled += self.reconcile()       # a creator that died since the last pass
        for tid, message in self.reconcile_errors[seen:]:
            self._tick_failed(tid, TaskError(500, message))
        names = os.listdir(self.tasks_dir) if os.path.isdir(self.tasks_dir) else []
        for name in sorted(n for n in names if TASK_ID_RE.fullmatch(n)):
            path = os.path.join(self.tasks_dir, name)
            try:
                info = self._derive(self._read_task(path), self._events(path))
            except (OSError, ValueError, KeyError, TypeError):
                continue                          # unreadable: list() shows it, nothing to retry
            if not info.get("retry_due") or self.running_count() >= self.max_running:
                continue
            try:
                if self._retry(path):
                    retried.append(name)
            except (TaskError, KeyError, OSError, ValueError, TypeError) as exc:
                self._tick_failed(name, exc)
        return retried

    def _retry(self, path: str) -> bool:
        """Send a thread whose verify failed back to its agent; False when it is not due after all
        or is not ready yet (nothing lost still running in its folder, a free place). A retry
        that can never go out (the agent is not offered any more, the worktree is gone) is put on
        record as blocked, so nothing waits on it, and raised."""
        with _locked(path):
            task = self._trusted(path)
            events = self._events(path)
            info = self._derive(task, events)
            if not info.get("retry_due"):
                return False
            why = self._retry_blocker(task)
            if why:
                self._append(path, {"ev": "blocked", "n": info["work_run"], "reason": why,
                                    "at": stamp()})
                raise TaskError(409, f"the verify retry is blocked: {why}")
            if task["runner"] not in self.runners:
                return False          # another config's agent: left due for a store that offers it
            if self._still_writing(task, events):
                return False
            v, tail = info["verify"], ""
            try:
                with open(os.path.join(path, f"verify-{v['n']}.log"), "rb") as handle:
                    handle.seek(max(0, os.path.getsize(os.path.join(path, f"verify-{v['n']}.log")) - 1500))
                    tail = handle.read().decode("utf-8", "replace").strip()
            except OSError:
                pass
            if v.get("timed_out"):
                what = (f"timed out after {secs_text(v.get('limit'))} s and was ended (exit {v['rc']})")
                ask = ("Find out why it runs so long (or hangs) and fix it so that command exits 0 "
                       f"within {secs_text(v.get('limit'))} s.")
            else:
                what = f"failed (exit {v['rc']})"
                ask = "Find out why and fix it so that command exits 0."
            text = (f"The verify command {what}:\n\n    {task['verify']}\n\n{tail}\n\n"
                    f"{ask} This is attempt "
                    f"{info['attempts'] + 1} of {1 + int(task.get('retries') or 0)}.")
            with self._capacity():
                if not self._has_room():
                    return False
                self._start_run(path, task, info["runs"] + 1, text, followup=True,
                                prev_rc=info["rc"], auto=True, prev_status=info["status"])
        return True

    def _retry_blocker(self, task: dict) -> str | None:
        """Why a follow-up on this thread can never go out; None when nothing stands in the way. An
        agent this store does not offer counts only when the thread was made with this store's
        config file (its record names it): a store built from another config, the CLI beside a
        bridge, must not call it gone, because the store the thread belongs to may offer it. A
        thread made with no config file (the agents are found on PATH, so each process has its own
        set, and a bridge started before an agent was installed has a stale one) has no owner to
        name: no store calls its agent gone, the retry stays due for the one that offers it. A
        record from before the config was recorded is judged as it always was."""
        if task["runner"] not in self.runners and self._owns(task):
            return f"the agent {task['runner']} is not offered any more"
        if task["mode"] == "worktree" and not os.path.isdir(task["worktree"] or ""):
            return "the task's worktree is gone"
        return None

    def _owns(self, task: dict) -> bool:
        """True when this store may say the thread's agent is gone (see _retry_blocker)."""
        if "config" not in task:
            return True
        return bool(task["config"]) and task["config"] == self.config_path

    def _tick_failed(self, tid: str, exc: Exception) -> None:
        """A retry that could not go out: on record in tick_errors for this pass, and handed to
        on_tick_error (the bridge audits it) once per distinct thread and message."""
        message = exc.message if isinstance(exc, TaskError) else f"{type(exc).__name__}: {exc}"
        message = message[:300]
        self.tick_errors.append((tid, message))
        if (tid, message) in self._tick_reported:
            return
        self._tick_reported.add((tid, message))
        if self.on_tick_error:
            with contextlib.suppress(OSError):
                self.on_tick_error(tid, message)

    @staticmethod
    def _run_groups(events: list, n: int) -> list:
        """Every process group run n put on record: its agent, its setup line, its verify line."""
        return list(dict.fromkeys(e.get("pid") for e in events
                                  if e["ev"] == "child" and e.get("n") == n))

    def _orphans(self, task: dict, events: list) -> dict:
        """{run: [process groups]} that runs whose supervisor died before the exit receipt left
        alive in the task folder: the agent, its setup or its verify, of a work run or a check."""
        ended = {e.get("n") for e in events if e["ev"] == "exit"}
        out = {}
        for e in events:
            n = e.get("n")
            if e["ev"] != "run" or not isinstance(n, int) or n in ended or n in out:
                continue
            if _pid_alive(e.get("pid"), task["id"]):
                continue                                  # its supervisor is alive and in charge
            live = [g for g in self._run_groups(events, n)
                    if _owned_group(g, task.get("cwd") or "")]
            if live:
                out[n] = live
        return out

    def _still_writing(self, task: dict, events: list) -> bool:
        """Something a lost run started (agent, setup, verify; work or check) still runs here."""
        return bool(self._orphans(task, events))

    def followup(self, tid: object, text: object) -> dict:
        path = self._dir(tid)
        text = self._check_text(text)
        with _locked(path):
            task = self._trusted(path)
            events = self._events(path)
            info = self._derive(task, events)
            if info["status"] in ("running", "checking", "stopping"):
                raise TaskError(409, "the agent is still working; wait for it or stop it")
            if self._still_writing(task, events):
                raise TaskError(409, "something of the lost agent still runs in its folder; stop it first")
            if info["status"] in CLOSED:
                raise TaskError(409, "this task is closed")
            if task["mode"] == "worktree" and not os.path.isdir(task["worktree"] or ""):
                raise TaskError(409, "the task's worktree is gone")
            if task["runner"] not in self.runners:
                raise TaskError(409, f"the agent {task['runner']} is not offered any more")
            # after a failed setup the agent never ran: its prompt says so, not "exit code 125"
            failed_setup = (info.get("setup") or {}) if info.get("setup_failed") else {}
            prev_setup = failed_setup.get("rc") if failed_setup else None
            limit = failed_setup.get("limit") if failed_setup.get("timed_out") else None
            with self._capacity():
                if not self._has_room():
                    raise TaskError(429, f"{self.max_running} tasks are already running")
                self._start_run(path, task, info["runs"] + 1, text, followup=True,
                                prev_rc=info["rc"], prev_setup=prev_setup,
                                prev_status=info["status"], prev_setup_limit=limit,
                                prev_cap=(info.get("cap") or 0) if info.get("killed") else None)
        return self.summary(path)

    def check(self, tid: object, runner: object, model: object = None, text: object = "") -> dict:
        """A second opinion from another agent: it reads the work, never edits, and ends with
        VERDICT: PASS|FAIL. Recorded as a run of its own; the task's status stays what the work made it."""
        path = self._dir(tid)
        if not isinstance(runner, str) or runner not in self.runners:
            raise TaskError(400, "pick one of the offered agents as the checker")
        if self.runners[runner].get("role") == "work":
            raise TaskError(400, f"{runner} does work but does not check it; pick another checker")
        text = "" if text in (None, "") else self._check_text(text)
        model = self._check_model(runner, model)
        with _locked(path):
            task = self._trusted(path)
            events = self._events(path)
            info = self._derive(task, events)
            if info["status"] in ("running", "checking", "stopping"):
                raise TaskError(409, "something is still running on this task")
            if self._still_writing(task, events):
                raise TaskError(409, "something of the lost agent still runs in its folder; stop it first")
            if info["status"] in CLOSED:
                raise TaskError(409, "this task is closed")
            if info["status"] == "new":
                raise TaskError(409, "nothing to check yet")
            if task["mode"] == "worktree" and not os.path.isdir(task["worktree"] or ""):
                raise TaskError(409, "the task's worktree is gone")
            with self._capacity():
                if not self._has_room():
                    raise TaskError(429, f"{self.max_running} tasks are already running")
                self._start_run(path, task, info["runs"] + 1, text, followup=False, runner=runner,
                                model=model, kind="check")
        return self.summary(path)

    def rename(self, tid: object, title: object) -> dict:
        """A new title for the thread, of any status: task.json is rewritten whole under the
        thread's own lock, the title the only change (it is not among the PINNED fields)."""
        path = self._dir(tid)
        new = self._check_title(title)
        if new is None:
            raise TaskError(400, "title must be one line of plain text, 90 characters at most")
        with _locked(path, wait=RENAME_WAIT):
            task = self._trusted(path)
            task["title"] = new
            self._write_json(os.path.join(path, "task.json"), task, indent=1)
        return self.summary(path)

    def stop(self, tid: object) -> dict:
        path = self._dir(tid)
        deadline = time.monotonic() + 5            # a run reports its agent's pid within moments of starting
        while time.monotonic() < deadline:
            events = self._events(path)
            n = max([e["n"] for e in events if e["ev"] == "run" and isinstance(e.get("n"), int)] or [0])
            if not n or any(e["ev"] in ("child", "exit") and e.get("n") == n for e in events):
                break
            time.sleep(0.1)
        with _locked(path):
            # a live supervisor is found by its own cmdline (--exec and this folder's id), never
            # by a path in the record: a record the agent altered cannot keep you from stopping it
            task = dict(self._read_task(path), id=tid)
            events = self._events(path)
            info = self._derive(task, events)
            n = info["runs"]
            if info["status"] == "stopping":
                return self.summary(path)          # asked already: one stop event, one signal
            if info["status"] in ("running", "checking"):
                run = next(e for e in reversed(events) if e["ev"] == "run" and e.get("n") == n)
                if not _pid_alive(run.get("pid"), tid):
                    raise TaskError(409, "the agent's supervisor just went away; refresh")
                try:                     # the supervisor ends the agent's whole group and writes the receipt
                    os.kill(int(run["pid"]), signal.SIGTERM)
                except (OSError, ValueError, TypeError) as exc:
                    raise TaskError(409, f"could not signal the agent: {exc}") from exc
                self._append(path, {"ev": "stop", "n": n, "at": stamp()})
            else:                   # a supervisor died: end what its run left in the folder
                task = self._trusted(path)        # its folder decides which groups are ours
                orphans = self._orphans(task, events)
                if not orphans:
                    raise TaskError(409, "nothing is running")
                groups = [g for gs in orphans.values() for g in gs]
                for g in groups:
                    with contextlib.suppress(OSError):
                        os.killpg(g, signal.SIGTERM)
                for _ in range(30):                       # give them 3 s, then end them
                    if not any(_owned_group(g, task["cwd"]) for g in groups):
                        break
                    time.sleep(0.1)
                else:
                    for g in groups:
                        with contextlib.suppress(OSError):
                            os.killpg(g, signal.SIGKILL)
                for lost in sorted(orphans):
                    self._append(path, {"ev": "stop", "n": lost, "at": stamp()})
                    self._append(path, {"ev": "exit", "n": lost, "rc": None, "killed": False,
                                        "stopped": True, "by": "bridge (the supervisor was gone)",
                                        "at": stamp()})
        return self.summary(path)

    # ---- reviewing -----------------------------------------------------------------------------
    def diff(self, tid: object) -> dict:
        path = self._dir(tid)
        task = self._trusted(path)                # git runs on the paths and refs it names
        info = self._derive(task, self._events(path))
        if task["mode"] == "worktree" and task.get("worktree") and os.path.isdir(task["worktree"]):
            why = _worktree_unsafe(task["worktree"], task["repo"])
            if why:
                raise TaskError(409, _unsafe_text(why))
            return self._git_diff(path, task["worktree"], task["base_sha"], []) | {"where": task["worktree"]}
        if task["mode"] == "worktree":
            merged = info["closed"] or {}
            branch_diff = _git(["diff", "--stat", task["base_sha"], task["branch"]], task["repo"]) if task.get("repo") else None
            return {"files": [], "patch": "", "where": task.get("branch"),
                    "note": ("merged as " + str(merged.get("sha", "?"))[:12] if info["status"] == "merged" else
                             f"worktree removed; the work is on branch {task.get('branch')}") +
                            ("\n" + branch_diff.stdout[-2000:] if branch_diff and branch_diff.returncode == 0 else "")}
        if task.get("repo") and task.get("base_sha"):
            out = self._git_diff(path, task["repo"], task["base_sha"], task.get("dirty_before") or [])
            return out | {"where": task["repo"]}
        return self._mtime_diff(task, info)

    def _git_diff(self, path: str, tree: str, base: str, dirty_before: list) -> dict:
        """Everything in `tree` (committed, staged, unstaged, untracked-not-ignored) against `base`,
        computed on a scratch copy of the index so the real index is never touched."""
        idx_path = _git(["rev-parse", "--git-path", "index"], tree, timeout=10).stdout.strip()
        idx_path = idx_path if os.path.isabs(idx_path) else os.path.join(tree, idx_path)
        fd, scratch = tempfile.mkstemp(prefix=".diff-index-", dir=path)
        os.close(fd)
        try:
            try:
                if os.path.isfile(idx_path):
                    shutil.copyfile(idx_path, scratch)
                else:
                    os.unlink(scratch)                     # git wants a real index or none at all
            except OSError as exc:
                raise TaskError(500, f"could not prepare the diff: {exc}") from exc
            env = {"GIT_INDEX_FILE": scratch}
            add = _git(["add", "-A"], tree, env=env)
            if add.returncode != 0:
                raise TaskError(502, "git add (scratch index) failed: " + _first_lines(add.stderr))
            plain = ["--no-ext-diff", "--no-textconv"]     # no command named in a config or an attribute runs for a view
            num = _git(["diff", "--cached", *plain, "--numstat", "-M", base], tree, env=env, errors="replace")
            names = _git(["diff", "--cached", *plain, "--name-status", "-M", base], tree, env=env, errors="replace")
            patch = _git(["diff", "--cached", *plain, "-M", "--no-color", base], tree, env=env, errors="replace")
            if num.returncode != 0 or patch.returncode != 0:
                raise TaskError(502, "git diff failed: " + _first_lines(num.stderr or patch.stderr))
        finally:
            try:
                os.unlink(scratch)                         # our own scratch file
            except OSError:
                pass
        status = {}
        for line in names.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                status[parts[-1]] = parts[0][:1]
        files = []
        for line in num.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            name = parts[2]
            if " => " in name:
                name = re.sub(r"\{([^{}]*) => ([^{}]*)\}", r"\2", name).replace("//", "/")
                name = name.split(" => ")[-1]
            files.append({"path": name, "add": None if parts[0] == "-" else int(parts[0]),
                          "del": None if parts[1] == "-" else int(parts[1]), "status": status.get(name, "M"),
                          "was_dirty": name in dirty_before})
        text = patch.stdout
        return {"files": files, "patch": text[:MAX_PATCH], "truncated": len(text) > MAX_PATCH,
                "note": ("some of these files had uncommitted changes before the task started (marked)"
                         if any(f["was_dirty"] for f in files) else "")}

    def _mtime_diff(self, task: dict, info: dict) -> dict:
        try:
            since = calendar.timegm(time.strptime(task["created"], "%Y-%m-%dT%H:%M:%SZ"))
        except (KeyError, ValueError, TypeError):
            since = 0
        files, seen, capped = [], 0, False
        for root, dirs, names in os.walk(task["cwd"]):
            dirs[:] = [d for d in dirs if d not in REGEN_DIRS]
            for n in names:
                seen += 1
                if seen > 20000 or len(files) >= 500:
                    capped = True
                    break
                p = os.path.join(root, n)
                try:
                    if os.path.getmtime(p) >= since:
                        files.append({"path": os.path.relpath(p, task["cwd"]), "add": None, "del": None, "status": "M"})
                except OSError:
                    continue
            if capped:
                break
        return {"files": files, "patch": "", "where": task["cwd"],
                "note": "not a git folder: these files were modified after the task started (no line diff)" +
                        (" · the walk stopped at its limit" if capped else "")}

    # ---- closing -------------------------------------------------------------------------------
    def close(self, tid: object, action: object, force: object = False,
              require_clear: bool = False) -> dict:
        """merge / discard / done. A merge the evidence argues against (merge_blockers) is refused
        unless force: the person saw the blockers and lands it anyway; the merge event says so.
        `require_clear` (a caller that may never close past a blocker, the phone) refuses every
        action, not only the merge, while anything is against the thread, judged under the same
        locks as the close itself: a verdict that lands after the caller last looked still stops."""
        path = self._dir(tid)
        if action not in ("merge", "discard", "done"):
            raise TaskError(400, "action must be merge, discard or done")
        if not isinstance(force, bool):
            raise TaskError(400, "force must be true or false")
        if require_clear and force:
            raise TaskError(400, "force cannot be asked of a close that must be clear")
        with _locked(path):
            task = self._trusted(path)
            # the repository stays locked for the whole merge or discard, whichever process asks
            with self._repo_lock(task.get("repo") if task.get("mode") == "worktree" else None):
                return self._close(path, tid, action, force, task, require_clear)

    def _close(self, path: str, tid: str, action: str, force: bool = False,
               task: dict | None = None, require_clear: bool = False) -> dict:
        task = self._trusted(path) if task is None else task
        events = self._events(path)
        info = self._derive(task, events)
        if info["status"] in ("running", "checking", "stopping"):
            raise TaskError(409, "the agent is still working; stop it first")
        if self._still_writing(task, events):
            raise TaskError(409, "something of the lost agent still runs in its folder; stop it first")
        if info["status"] in CLOSED:
            raise TaskError(409, f"already {info['status']}")
        if require_clear:
            self._read_verdicts(path, info)
            now, gap = self._live_digest(task, info)
            against = self._merge_blockers(info, now, self.merge_requires_check, gap=gap)
            wt = task.get("worktree") or ""
            unsafe = (_worktree_unsafe(wt, task.get("repo") or "")
                      if task.get("mode") == "worktree" and os.path.isdir(wt) else None)
            if unsafe:
                against.insert(0, _unsafe_text(unsafe))
            if against:
                raise TaskError(409, "the evidence says not yet: " + "; ".join(against) +
                                     ". Nothing was changed")
        if action == "done":
            if task["mode"] == "worktree" and os.path.isdir(task.get("worktree") or ""):
                raise TaskError(400, "a worktree task is closed by merge or discard")
            self._append(path, {"ev": "done", "at": stamp()})
            return self.summary(path)
        if task["mode"] != "worktree":
            raise TaskError(400, "this task worked in place; there is nothing to merge or discard (mark it done)")
        wt, repo, branch = task["worktree"], task["repo"], task["branch"]
        has_wt = os.path.isdir(wt or "")
        if _git(["rev-parse", "-q", "--verify", f"refs/heads/{branch}"], repo).returncode != 0:
            raise TaskError(409, f"branch {branch} is gone; nothing to merge or set aside (mark the task done)")
        unsafe = _worktree_unsafe(wt, repo) if has_wt else None     # the agent repointed its worktree's .git
        if unsafe and action == "merge":
            raise TaskError(409, _unsafe_text(unsafe) + ". Nothing was merged")
        if has_wt and not unsafe:
            self._require_branch(task)
        if action == "discard":
            committed, skipped = (False, None) if (unsafe or not has_wt) else self._commit_leftovers(task)
            gone = self._remove_worktree(path, task, has_wt, unsafe)
            if gone.get("error"):
                raise TaskError(502, gone["error"])
            self._append(path, {"ev": "discarded", "branch": branch, "committed": committed, **gone,
                                **self._skipped_field(skipped), "hooks": "off", "at": stamp()})
            notes = self._kept_note(gone) + self._skipped_note(skipped, gone)
            said = f"set aside: the work stays on branch {branch}"
            return self.summary(path) | {"message": said + notes}
        # merge: every check that can refuse runs before anything is written
        self._read_verdicts(path, info)
        now, gap = self._live_digest(task, info)
        blockers = self._merge_blockers(info, now, self.merge_requires_check, gap=gap)
        if blockers and not force:
            raise TaskError(409, "the evidence says not yet: " + "; ".join(blockers) +
                                 ". Merge anyway (force) to land it regardless")
        evidence = {"verify": (info.get("verify") or {}).get("rc"),
                    "check": (info.get("held") or info.get("check") or {}).get("verdict"),
                    "forced": bool(blockers)}
        busy = _unfinished(repo)
        if busy:                  # theirs to finish: we merge onto a settled branch or not at all
            raise TaskError(409, f"your repo has an unfinished {busy}; finish it or abort it "
                                 "yourself, then merge again. Nothing was changed")
        if not task.get("base_branch"):
            raise TaskError(409, "the repo was on a detached HEAD when the task started; merge the branch by hand: " + branch)
        cur = _git(["symbolic-ref", "--short", "-q", "HEAD"], repo).stdout.strip()
        if cur != task["base_branch"]:
            raise TaskError(409, f"your repo is on {cur or 'a detached HEAD'} now; switch back to {task['base_branch']} to merge")
        if self.no_ai_trailers:
            bodies = _git(["log", "--format=%B", f"{task['base_sha']}..{branch}"], repo)
            if bodies.returncode == 0 and TRAILER_RE.search(bodies.stdout):
                raise TaskError(409, f"the agent's commits on {branch} carry an AI attribution trailer (Co-Authored-By / Generated with); "
                                     "reword them or set the task aside")
        committed, skipped = self._commit_leftovers(task) if has_wt else (False, None)
        tip = _git(["rev-parse", f"refs/heads/{branch}"], repo).stdout.strip()
        ahead = _git(["rev-list", "--count", f"{task['base_sha']}..{branch}"], repo)
        if ahead.returncode == 0 and ahead.stdout.strip() == "0":
            gone = self._remove_worktree(path, task, has_wt)
            self._append(path, {"ev": "done", "note": "no changes to merge", **gone,
                                **self._skipped_field(skipped), "at": stamp()})
            said = "the agent changed nothing, so there was nothing to merge"
            notes = self._kept_note(gone) + self._skipped_note(skipped, gone)
            return self.summary(path) | {"message": said + notes}
        title = re.sub(r"\s+", " ", task.get("title", ""))[:72]
        merge = _git([*NO_HOOKS, *_identity(repo), "merge", "--no-edit", *_merge_no_verify(), "-m",
                      f"Merge container-map task {tid}: {title}", branch], repo, timeout=120)
        if merge.returncode != 0:
            # abort only the merge this call began (MERGE_HEAD is the branch it merged); a repo
            # with an unfinished one of its own was refused above
            pending = _git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], repo)
            if pending.returncode == 0 and pending.stdout.strip() == tip:
                _git(["merge", "--abort"], repo)
            kept = f" (the agent's uncommitted changes were committed onto {branch} first)" if committed else ""
            raise TaskError(409, "git refused the merge; your branch is unchanged" + kept + ": " + _first_lines(merge.stderr or merge.stdout))
        sha = _git(["rev-parse", "HEAD"], repo).stdout.strip()
        gone = self._remove_worktree(path, task, has_wt)    # the merge landed: noted, never raised
        self._append(path, {"ev": "merged", "sha": sha, "branch": branch, "into": task["base_branch"],
                            "committed": committed, **gone, **self._skipped_field(skipped),
                            "evidence": evidence, "hooks": "off", "at": stamp()})
        notes = self._kept_note(gone) + self._skipped_note(skipped, gone)
        said = f"merged into {task['base_branch']} at {sha[:10]} (the repository's git hooks were not run)"
        return self.summary(path) | {"message": said + notes}

    @staticmethod
    def _require_branch(task: dict) -> None:
        """The agent's commits are merged (or kept) by the branch the thread recorded; a worktree
        it left on another branch or a detached HEAD would lose them, so neither action runs."""
        wt, branch = task["worktree"], task["branch"]
        head = _git(["symbolic-ref", "-q", "HEAD"], wt)
        if head.returncode not in (0, 1):                # 1 = HEAD is detached
            raise TaskError(502, "could not read the worktree's HEAD: " + _first_lines(head.stderr))
        on = head.stdout.strip()
        if on == f"refs/heads/{branch}":
            return
        where = "a detached HEAD"
        if on.startswith("refs/heads/"):
            where = "branch " + on[len("refs/heads/"):]
        raise TaskError(409, f"the agent left its worktree on {where}, not on {branch}, and "
                             f"merging or setting aside only keeps what is on {branch}. Nothing "
                             f"was merged or removed. Put its commits on {branch} (merge or "
                             f"cherry-pick them there), then run: git -C {wt} switch {branch}")

    def _remove_worktree(self, path: str, task: dict, has_wt: bool, unsafe: str | None = None) -> dict:
        """Take the thread's worktree away without losing anything. `git worktree remove` deletes
        the ignored and untracked files with it, so when the worktree holds any that are not build
        byproducts (_regenerable), or tracked changes, or git cannot say, the whole worktree is
        moved aside to <worktree>.retired-<stamp> and git forgets it; byproducts alone go with it
        (forced: they are untracked or ignored, not part of the branch). A removal git then fails
        (a read-only build folder) moves it aside too. Returns the fields the close event records;
        `error` while it is still there. `unsafe` (_worktree_unsafe) is why no git may run in it:
        it is then moved aside whole, unread."""
        wt, repo = task["worktree"], task["repo"]
        if not has_wt:
            _git(["worktree", "prune"], repo)
            return {"worktree_removed": True}
        left = None if unsafe else _leftovers(wt)
        why = unsafe
        subs = None if unsafe else _submodules(wt)
        if subs is None and not unsafe:
            why = "git could not say whether the worktree holds a submodule"
        elif subs:
            # forced, git would delete the submodules' own ignored files with them: never forced
            why = ("the worktree holds an initialized submodule (" + ", ".join(subs[:3])
                   + (" ..." if len(subs) > 3 else "") + "), which git will not remove with it")
        if left is not None and not left["kept"] and not left["dirty"] and not why:
            rm = _git(["worktree", "remove", *(["--force"] if left["junk"] else []), wt], repo)
            if rm.returncode == 0:
                return {"worktree_removed": True}
            why = "git could not remove the worktree: " + _first_lines(rm.stderr)
        base = wt + ".retired-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        aside, i = base, 1
        while os.path.lexists(aside):
            i += 1
            aside = f"{base}-{i}"
        try:
            os.rename(wt, aside)
        except OSError as exc:
            return {"worktree_removed": False,
                    "error": (why + "; " if why else "") + f"could not move the worktree aside: {exc}"}
        _git(["worktree", "prune"], repo)          # its admin entry points at a folder that moved
        kept = (left["kept"][:20] if left is not None
                else ["(all of it: git is not run in a worktree whose .git was changed)"] if unsafe
                else ["(git could not list them; all kept)"])
        out = {"worktree_removed": True, "retired": aside, "kept": kept}
        if left is not None and left["dirty"] and not out["kept"]:
            out["kept"] = ["(tracked changes that are not on the branch)"]
        if why:
            out["why"] = why
        return out

    @staticmethod
    def _kept_note(gone: dict) -> str:
        if gone.get("retired") and gone.get("why"):
            return f"; {gone['why']} (moved aside to {gone['retired']} instead)"
        if gone.get("retired"):
            names = ", ".join(gone.get("kept") or [])[:200]
            return (f"; files git would have deleted ({names}) were kept in "
                    f"{gone['retired']}")
        if gone.get("error"):
            return f"; the worktree is still there: {gone['error']}"
        return ""

    @staticmethod
    def _skipped_field(skipped: dict | None) -> dict:
        return {"skipped": skipped} if skipped else {}

    @staticmethod
    def _skipped_note(skipped: dict | None, gone: dict) -> str:
        if not skipped:
            return ""
        more = skipped["count"] - len(skipped["paths"])
        # what the worktree then kept is named by _kept_note; the rest left with the worktree
        gone_too = gone.get("worktree_removed") and not gone.get("retired")
        fate = " and removed with the worktree" if gone_too else ""
        return (f"; skipped build junk, not committed{fate} ({skipped['count']} files): "
                + ", ".join(skipped["paths"]) + (" ..." if more > len(skipped["paths"]) else ""))

    def _commit_leftovers(self, task: dict) -> tuple[bool, dict | None]:
        """Commit whatever the agent left uncommitted onto the thread's branch: (committed,
        skipped). Tracked changes always go; untracked files a merge_skip pattern names do not,
        and `skipped` says which ({"count", "paths"}; None when there were none)."""
        wt = task["worktree"]
        skip, stay = self._split_untracked(wt)
        if skip:
            # `add -A` minus the skipped: tracked changes, then each untracked file that stays
            add = _add_kept(wt, stay)
        else:
            add = _git(["add", "-A"], wt)
        if add.returncode != 0:
            raise TaskError(502, "git add in the worktree failed: " + _first_lines(add.stderr))
        skipped = _skip_units(skip, self.merge_skip) if skip else None
        if _git(["diff", "--cached", "--quiet"], wt).returncode == 0:
            return False, skipped
        msg = re.sub(r"\s+", " ", task.get("title", "task"))[:72]
        done = _git([*NO_HOOKS, *_identity(wt), "commit", "-q", "--no-verify", "-m", msg, "-m",
                     f"container-map task {task['id']}"], wt, timeout=120)
        if done.returncode != 0:
            raise TaskError(409, f"git would not commit the agent's uncommitted changes onto {task.get('branch')}; "
                                 "nothing was merged or removed: " + _first_lines(done.stderr or done.stdout))
        return True, skipped

    def _split_untracked(self, wt: str) -> tuple[list[str], list[str]]:
        """(skip, stay): the untracked files `git add -A` would add, split by merge_skip. Git's own
        matching decides: the patterns are exclude rules for a second listing, so what only the
        first lists is skipped. A tracked file is in neither. Two listings, each walked once.
        When git cannot list, nothing is committed: the skip list is not guessed at."""
        if not self.merge_skip:
            return [], []
        every = _git(["ls-files", "--others", "--exclude-standard", "-z"], wt, timeout=60)
        kept = _git(["ls-files", "--others", "--exclude-standard", "-z",
                     *[f"--exclude={p}" for p in self.merge_skip]], wt, timeout=60)
        for listing in (every, kept):
            if listing.returncode != 0:
                raise TaskError(502, "git could not list the untracked files, so the merge_skip "
                                     "list could not be applied; nothing was committed: "
                                     + (_first_lines(listing.stderr) or "git timed out"))
        stay = [p for p in kept.stdout.split("\x00") if p]
        staying = set(stay)
        return sorted(p for p in every.stdout.split("\x00") if p and p not in staying), stay


def store_for(cfg: dict, roots, tasks_dir: str | None = None, runners=None) -> TaskStore:
    """The task store a loaded config describes. The bridge and `cli.py thread` both build theirs
    here, so a thread started from either is the other's to read, tick and close. `runners` given
    (tests): exactly those, no systemd scope and no configured setup lines."""
    own = runners is None
    configured = cfg.get("tasks_dir")
    # the configured worktrees_dir belongs to the configured store: a store somewhere else
    # (a test's, a copy) puts its worktrees beside itself
    same = not tasks_dir or (bool(configured) and os.path.realpath(os.path.expanduser(tasks_dir))
                             == os.path.realpath(os.path.expanduser(configured)))
    return TaskStore(tasks_dir or configured,
                     runners_for(cfg) if own else valid_runners(runners), roots,
                     no_ai_trailers=bool(cfg.get("no_ai_trailers")),
                     scope=bool(cfg.get("task_scope", True)) and own,
                     worktree_setup=cfg.get("worktree_setup") if own else None,
                     merge_skip=cfg.get("merge_skip"),
                     merge_requires_check=bool(cfg.get("merge_requires_check")),
                     worktrees_dir=(cfg.get("worktrees_dir") or None) if same else None,
                     config_path=cfg.get("_path") or None)


# ---- the supervisor: `python3 tasks.py --exec DIR N CAP INLINE ID -- argv…` ----------------------
def _receipt(path: str, row: dict) -> None:
    with _locked(path):                     # waits until the bridge has recorded the run itself
        TaskStore._append(path, row)


_OWN_SCOPE = re.compile(r"/cm-task-[a-z0-9-]+\.scope$", re.M)


def _scope_unit(tid: str, n: int) -> re.Pattern:
    """The scope scope_prefix names for run n of this thread, and no other."""
    return re.compile(rf"/cm-task-{re.escape(tid[-6:])}-{int(n)}-[0-9a-f]+\.scope$", re.M)


def _scope_members(tid: str, n: int, proc: str = "/proc") -> list[int]:
    """pids in this supervisor's own cgroup (the systemd scope it was launched in), itself excluded."""
    try:
        with open(f"{proc}/self/cgroup", encoding="utf-8") as handle:
            mine = handle.read().strip()
    except OSError:
        return []
    # only the scope scope_prefix made for this very run is ours. init.scope (every WSL process)
    # or a login session-N.scope is shared, and so is another thread's cm-task scope this
    # supervisor merely inherited: sweeping any of them kills the agents and shells around it
    if not _scope_unit(tid, n).search(mine):
        return []
    out = []
    try:
        names = os.listdir(proc)
    except OSError:
        return []
    for name in names:
        if not name.isdigit() or int(name) == os.getpid():
            continue
        try:
            with open(f"{proc}/{name}/cgroup", encoding="utf-8") as handle:
                if handle.read().strip() == mine:
                    out.append(int(name))
        except OSError:
            continue
    return out


def _sweep_groups(pgids, scope: tuple | None = None, proc: str = "/proc") -> int:
    """SIGTERM, then SIGKILL, anything still alive in these process groups or in the supervisor's
    scope (a daemon it forked, a setsid escapee). `scope` is (tid, n) when the store launched
    this supervisor in its own scope, else None: no scope is swept. Returns how many."""
    def alive():
        pids = set()
        for pgid in pgids:
            pids.update(_group_pids(pgid, proc))
        if scope:
            pids.update(_scope_members(scope[0], scope[1], proc))
        pids.discard(os.getpid())
        return sorted(pids)
    found = alive()
    if not found:
        return 0
    for sig, grace in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 1.0)):
        for pid in alive():
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        end = time.monotonic() + grace
        while time.monotonic() < end and alive():
            time.sleep(0.1)
        if not alive():
            break
    return len(found)


def _forget_empty(groups: list) -> None:
    """Drop the groups with nobody left in them. An empty group stays empty (nothing can join
    it), but its number is free for any other process to reuse, so the final sweep must not look
    at it again."""
    groups[:] = [g for g in groups if _group_pids(g)]


def _sweep_after(child, scope: tuple | None = None, proc: str = "/proc") -> int:
    """After the agent exits on its own: what is still alive in its process group (and scope)."""
    return _sweep_groups([child.pid], scope, proc)


def _end_group(child) -> int:
    """SIGTERM the child's whole process group, SIGKILL whatever is left; the child's exit code.
    Every wait is bounded: a child that survives SIGKILL for REAP_GRACE more seconds (stuck in
    the kernel) is given up on, as -9, so the supervisor still writes its receipt."""
    rc = None
    for sig, grace in ((signal.SIGTERM, TERM_GRACE), (signal.SIGKILL, KILL_GRACE)):
        try:
            os.killpg(child.pid, sig)
        except OSError:
            pass
        try:
            rc = child.wait(timeout=grace)
            break
        except subprocess.TimeoutExpired:
            continue
    if rc is None:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            rc = child.wait(timeout=REAP_GRACE)
        except subprocess.TimeoutExpired:
            rc = -signal.SIGKILL
    # stragglers that ignored SIGTERM (or, with no way to list them, assume there are some)
    if _group_pids(child.pid) or not (_has_proc() or shutil.which("ps")):
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except OSError:
            pass
    return rc


def _run_unrecorded(path: str, n: int, state: dict, wait: float | None = None) -> str | None:
    """Why this supervisor may not start run n yet, None once it may: the store appends the `run`
    event (with the supervisor's pid) right after it launches it, and a supervisor that started its
    agent before that could outlive a creator that died in between, with recovery then writing a
    run and an exit receipt of its own over a live agent. So nothing starts until the run is on
    record, and nothing starts if recovery has closed it (a run with no process, or an exit)
    first. A stop that arrives while waiting ends the wait: it has its own early exit."""
    deadline = time.monotonic() + (LAUNCH_WAIT if wait is None else wait)
    while True:
        rows = TaskStore._events(path)
        if any(e["ev"] == "exit" and e.get("n") == n for e in rows):
            return "recovery closed this run before it started"
        run = next((e for e in rows if e["ev"] == "run" and e.get("n") == n), None)
        if run is not None:
            return None if run.get("pid") else "recovery wrote this run without a process"
        if state["stopped"]:
            return None
        if time.monotonic() >= deadline:
            return "its run was never put on record (the process that launched it is gone)"
        time.sleep(0.02)


def _supervise(argv: list[str]) -> int:
    state = {"stopped": False}

    def on_term(signum, frame):              # the bridge's stop
        state["stopped"] = True
    signal.signal(signal.SIGTERM, on_term)   # first thing: a stop that arrives this early is never fatal
    try:
        sep = argv.index("--")
        head = argv[:sep]
        path, n, cap, inline, tid = head[:5]
        verify = head[5] if len(head) > 5 else ""        # the lines themselves; "" = none
        setup = head[6] if len(head) > 6 else ""
        scoped = len(head) > 7 and head[7] == "1"
        digest = head[8] if len(head) > 8 else ""        # "v": verify receipt, "c": exit receipt
        skip = DEFAULT_MERGE_SKIP                        # the store's merge_skip (JSON), if given
        if len(head) > 9:
            with contextlib.suppress(ValueError):
                skip = valid_merge_skip(json.loads(head[9]))
        verify_cap, setup_cap = VERIFY_TIMEOUT, SETUP_TIMEOUT      # seconds; the store's, if given
        with contextlib.suppress(ValueError):
            if len(head) > 10:
                verify_cap = float(head[10])
            if len(head) > 11:
                setup_cap = float(head[11])
        cmd = argv[sep + 1:]
        n, cap = int(n), int(cap)
    except (ValueError, IndexError):
        print("usage: tasks.py --exec DIR N CAP INLINE ID [VERIFY [SETUP [SCOPED [DIGEST [SKIP "
              "[VERIFY_SECONDS [SETUP_SECONDS]]]]]]]] -- argv...", file=sys.stderr)
        return 2
    try:
        log = open(os.path.join(path, f"run-{n}.log"), "a", encoding="utf-8", buffering=1)
    except OSError:
        _receipt(path, {"ev": "exit", "n": n, "rc": 127, "killed": False, "stopped": False, "at": stamp(),
                        "error": "could not open the run log"})
        return 127
    refused = _run_unrecorded(path, n, state)
    if refused:                              # nothing started, nothing to receipt: the record is not ours to write
        log.write(f"# run {n} · not started · {refused} · {stamp()}\n")
        log.close()
        return 3
    log.write(f"# run {n} · {os.path.basename(cmd[0]) if cmd else '?'} · in {os.getcwd()} · started {stamp()}\n")
    groups: list[int] = []                   # every process group this run records
    if state["stopped"]:
        log.write(f"\n# run {n} · exit -15 · stopped by you before the agent started · {stamp()}\n")
        log.close()
        _receipt(path, {"ev": "exit", "n": n, "rc": -15, "killed": False, "stopped": True, "at": stamp()})
        return 0
    if setup:
        try:                                 # a fresh worktree's setup line, before the agent ever starts
            src = _shell_line(path, n, log, state, "setup", setup, setup_cap, groups)
        except Exception as exc:             # never lose the exit receipt over the setup
            src = _could_not_run(path, n, log, "setup", exc)
        if src not in (None, 0) or state["stopped"]:
            why = "stopped by you during setup" if state["stopped"] else f"setup failed (exit {src})"
            if not state["stopped"] and "setup" in state.get("timed_out", {}):
                why += f" · timed out after {secs_text(state['timed_out']['setup'])} s"
            log.write(f"\n# run {n} · exit 125 · {why} · no agent started · {stamp()}\n")
            _final_sweep(log, n, groups, (tid, n) if scoped else None)
            log.close()
            _receipt(path, {"ev": "exit", "n": n, "rc": 125, "killed": False, "stopped": state["stopped"],
                            "setup_failed": True, "at": stamp()})
            return 0
    try:                                     # its own group: stop and the cap end everything it starts
        stdin = subprocess.DEVNULL if inline == "1" else open(os.path.join(path, f"prompt-{n}.txt"), "rb")
        child = subprocess.Popen(cmd, stdin=stdin, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as exc:
        log.write(f"could not start the agent: {exc}\n# run {n} · exit 127 · {stamp()}\n")
        _final_sweep(log, n, groups, (tid, n) if scoped else None)    # what the setup line left behind
        log.close()
        _receipt(path, {"ev": "exit", "n": n, "rc": 127, "killed": False, "stopped": False, "at": stamp()})
        return 127
    groups.append(child.pid)
    _receipt(path, {"ev": "child", "n": n, "pid": child.pid, "at": stamp()})
    deadline = time.monotonic() + cap
    rc, killed = None, False
    while rc is None:
        try:
            rc = child.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            if state["stopped"] or time.monotonic() > deadline:
                killed = not state["stopped"]
                rc = _end_group(child)
    left = _sweep_after(child, (tid, n) if scoped else None)   # nothing it left outlives it
    _forget_empty(groups)
    why =" · stopped by you" if state["stopped"] else (" · KILLED after the time cap" if killed else "")
    log.write(f"\n# run {n} · exit {rc}{why}{(' · ended ' + str(left) + ' leftover process' + ('es' if left > 1 else '')) if left else ''} · {stamp()}\n")
    # the oracle runs after any exit of the agent's own (not a stop, not the cap): an agent that
    # crashes after doing the work still gets its work judged, and the verify decides the status
    if verify and not state["stopped"] and not killed:
        try:                                 # one shell line in the task folder, its own receipt
            vrc = _shell_line(path, n, log, state, "verify", verify, verify_cap, groups,
                              skip if digest == "v" else None)
            if vrc == 0 and rc != 0:
                log.write(f"# run {n} · the agent exited {rc}, but the verify passed: the work counts\n")
        except Exception as exc:             # never lose the exit receipt over the check
            _could_not_run(path, n, log, "verify", exc)
    _final_sweep(log, n, groups, (tid, n) if scoped else None)
    log.close()
    row = {"ev": "exit", "n": n, "rc": rc, "killed": killed, "stopped": state["stopped"],
           "at": stamp()}
    if digest == "c" and rc == 0:            # what the checker left behind is what it vouched for
        row["digest"] = _digest_here(path, skip)
    _receipt(path, row)
    return 0


def _final_sweep(log, n: int, groups: list, scope: tuple | None) -> None:
    """Nothing this run started may outlive its receipt: every process group it recorded and its
    systemd scope are swept before ANY exit receipt is written, the early ones too (a setup line
    that detached a helper with setsid and then failed, an agent that could not start after a
    setup that left one): once a run has its exit receipt, _orphans no longer looks at it."""
    late = _sweep_groups(groups, scope)
    if late:
        with contextlib.suppress(OSError, ValueError):
            log.write(f"# run {n} · ended {late} more leftover process{'es' if late > 1 else ''} "
                      f"before the receipt · {stamp()}\n")


def _digest_here(path: str, skip: list[str]) -> str | None:
    """_work_digest of the folder this supervisor runs in, for the receipt. In a thread's worktree
    it is taken at the worktree's root, not wherever the agent's folder is (a .git it made in a
    subfolder would otherwise be the repository), and not at all when the agent changed the
    worktree's own .git (_worktree_unsafe): the receipt then carries no digest and the card
    names the problem."""
    try:
        task = TaskStore._read_task(path)
    except (OSError, ValueError):
        task = {}
    if task.get("mode") == "worktree" and task.get("worktree"):
        if _worktree_unsafe(task["worktree"], task.get("repo") or ""):
            return None
        return _work_digest(task["worktree"], skip)
    return _work_digest(os.getcwd(), skip)


def secs_text(value: object) -> str:
    """A time cap in seconds as a person reads it: 600, not 600.0."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _could_not_run(path: str, n: int, log, which: str, exc: Exception) -> int:
    """A setup or verify line that could not even run (its log unwritable, no /bin/sh) still
    leaves a receipt, rc 127 with the error, so it blocks the merge and counts as failed; unless
    the line ran and left its own receipt before the failure. Returns the rc on record."""
    with contextlib.suppress(OSError, ValueError):
        log.write(f"# {which} failed to run: {exc}\n")
    mine = [e for e in TaskStore._events(path) if e["ev"] == which and e.get("n") == n]
    if mine:
        return mine[-1].get("rc")
    _receipt(path, {"ev": which, "n": n, "rc": 127, "seconds": 0, "error": str(exc)[:200],
                    "at": stamp()})
    return 127


def _shell_line(path: str, n: int, log, state: dict, which: str, command: str, timeout: float,
                groups: list | None = None, digest: list[str] | None = None):
    """The task's `which` line ("verify" or "setup", as the store passed it in argv) in the task
    folder, with its own receipt (<which>-N.log and a <which> event; with `digest` (the merge_skip
    patterns), the receipt carries the folder's content digest taken right after the line ended,
    leaving those untracked files out). Its exit code; None
    when there is no line. The line runs in a process group of its own; whatever it left running
    in that group (a background helper) is ended before the receipt, whatever the exit code, and
    the group is added to `groups` for the supervisor's last sweep."""
    if not command.strip():
        return None
    started = time.monotonic()
    timed_out = False
    log.write(f"# {which} · {command} · {stamp()}\n")
    with open(os.path.join(path, f"{which}-{n}.log"), "w", encoding="utf-8") as out:
        out.write(f"$ {command}\n")
        out.flush()
        child = subprocess.Popen(["/bin/sh", "-c", command], stdin=subprocess.DEVNULL, stdout=out,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        if groups is not None:
            groups.append(child.pid)
        # its group on record, so a stop after this supervisor dies can still end it
        with contextlib.suppress(OSError):
            _receipt(path, {"ev": "child", "n": n, "pid": child.pid, "kind": which, "at": stamp()})
        rc = None
        while rc is None:
            try:
                rc = child.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                if state["stopped"] or time.monotonic() - started > timeout:
                    timed_out = not state["stopped"]
                    rc = _end_group(child)
        if timed_out:
            out.write(f"\n$ timed out after {secs_text(timeout)} s\n")
        out.write(f"\n$ exit {rc}\n")
    seconds = round(time.monotonic() - started, 1)
    left = _sweep_groups([child.pid])
    if groups is not None:
        _forget_empty(groups)
    cap = f" · timed out after {secs_text(timeout)} s" if timed_out else ""
    log.write(f"# {which} · exit {rc} · {seconds}s{cap}\n")
    if left:
        log.write(f"# {which} · ended {left} leftover process{'es' if left > 1 else ''}\n")
    row = {"ev": which, "n": n, "rc": rc, "seconds": seconds, "at": stamp()}
    if timed_out:                        # the cap ended it: not a failure of the line's own
        row.update(timed_out=True, limit=timeout)
        state.setdefault("timed_out", {})[which] = timeout
    if digest is not None and not state["stopped"]:
        row["digest"] = _digest_here(path, digest)
    _receipt(path, row)
    return rc


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--exec":
        raise SystemExit(_supervise(sys.argv[2:]))
    print(__doc__)
