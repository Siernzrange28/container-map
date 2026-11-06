"""testenv.py: imported first by the test modules whose results a caller's environment could change.

A running map exports some of these (the service unit, a thread's agent, a tmux pane): any of them
points the tool at a live map instead of the fixture a test built, or moves its port. A test never
steers by what the caller happened to export, so they are removed here, before the tool's own
modules read them at import. CONTAINER_OUT stays: the tests honour it on purpose, so a test run
can leave a tracked page alone.

The same goes for git. A fixture repo is built and committed to by git, and the global config of
whoever runs the suite (a signing key, a hooks folder that refuses commits, merge.ff=only, another
default branch, useConfigOnly, autocrlf ...) changed what those calls did: tests passed on one
machine and errored on a buyer's. HOME and XDG_CONFIG_HOME point at a scratch folder with an empty
gitconfig for the whole run, the system config is off, and the GIT_ variables that choose a
repository or a config are removed. Fixtures that need an identity set one themselves.
"""
import atexit
import os
import shutil
import subprocess
import tempfile

STEERING = ("CONTAINER_ROOT", "CONTAINER_CONFIG", "CONTAINER_RENDER_SECONDS", "TMUX",
            "BRIDGE_HOST", "BRIDGE_PORT", "BRIDGE_LIVE_PATH", "BRIDGE_LOG_PATH", "BRIDGE_GAME_PATH",
            "BRIDGE_SEATS_PATH", "BRIDGE_RENDER_PATH")

for _name in STEERING:
    os.environ.pop(_name, None)
# An install's own renamed ids (legacy-ids.json beside themepacks.py, never shipped) are not a test's
# business: every test sees the public behaviour, a tool with no old ids. A test of the mechanism names its own file.
os.environ["CONTAINER_LEGACY_IDS"] = os.devnull

GIT_AIMING = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
              "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_PREFIX",
              "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_TEMPLATE_DIR",
              "GIT_ATTR_NOSYSTEM", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS")


def _hermetic_git():
    """HOME and XDG_CONFIG_HOME at a scratch folder holding an empty ~/.gitconfig, no system git
    config, and none of the GIT_ variables that pick a repository or a config. Done once per
    process, before any test runs; the folder goes at exit."""
    home = tempfile.mkdtemp(prefix="cm-test-home-")
    atexit.register(shutil.rmtree, home, True)
    with open(os.path.join(home, ".gitconfig"), "w", encoding="utf-8"):
        pass
    for name in list(os.environ):
        if name in GIT_AIMING or name.startswith("GIT_CONFIG_"):
            del os.environ[name]
    os.environ.update(HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"),
                      GIT_CONFIG_NOSYSTEM="1")


_hermetic_git()


def hermetic_repo(path):
    """Repo-level git config for a fixture repo, so what a test does there never depends on the
    global config of whoever runs it: with commit.gpgsign=true in ~/.gitconfig every fixture
    commit (and every commit the tool makes in the fixture) asked for a signing key and failed.
    The repo's own config wins over the global one for the tool's git calls as well as the
    test's. Returns `path`."""
    for key, value in (("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
        subprocess.run(["git", "config", key, value], cwd=path, check=True, capture_output=True)
    return path
