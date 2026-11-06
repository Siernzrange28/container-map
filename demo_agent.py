#!/usr/bin/env python3
"""demo_agent.py — the scripted agent behind `container-map demo`. No model, no network, no login.

It reads the thread's prompt on stdin and works in its current directory (the thread's worktree).
todo-app: the first attempt adds todo.complete() with an off-by-one, so the verify command fails;
when the prompt says the verify command failed it fixes the line properly. notes-site: it writes
the section the README's TODO line asks for. `--check` reviews the folder by running the tests and
ends with a VERDICT line, as the thread engine's checks do. Any other task is refused, loudly.

It only works inside a thread's own worktree. The page can also hand a parked item to any
configured runner, and that run starts in the tool's folder: this script refuses there, so it can
never edit the tool or a repo's main branch.
"""
import os
import subprocess
import sys
import time

COMPLETE = '''

def complete(items, n):
    """Mark the first n open items done; return a new list."""
    left = n - 1
    out = []
    for item in items:
        if not item["done"] and left > 0:
            item = dict(item, done=True)
            left -= 1
        out.append(item)
    return out
'''
SECTION = """## Getting started

Open `index.html` in a browser; there is no build step. Edit the text in place and reload.
"""


def say(line):
    print(line, flush=True)
    time.sleep(0.15)                        # a person can read the log as it streams


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def linked_branch(folder):
    """The branch a linked git worktree at `folder` has checked out, "" when it is not one. Its
    .git is a file naming the admin folder, whose HEAD names the branch; both are read as files,
    so no GIT_ variable of the caller can aim this at another repository."""
    try:
        with open(os.path.join(folder, ".git"), encoding="utf-8") as handle:
            line = handle.readline().strip()
        if not line.startswith("gitdir:"):
            return ""
        admin = os.path.join(folder, line[len("gitdir:"):].strip())
        with open(os.path.join(admin, "HEAD"), encoding="utf-8") as handle:
            head = handle.readline().strip()
    except OSError:
        return ""
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else ""


def in_thread_worktree():
    """True when the current folder is inside a worktree the thread engine made: a linked worktree
    on branch cm/<its own folder name> (where it puts new ones, beside the receipts), or a folder
    named worktree with task.json next to it (the older layout, inside the thread's receipts)."""
    here = os.path.realpath(os.getcwd())
    while True:
        parent = os.path.dirname(here)
        name = os.path.basename(here)
        if name == "worktree" and os.path.isfile(os.path.join(parent, "task.json")):
            return True
        if linked_branch(here) == "cm/" + name:
            return True
        if parent == here:
            return False
        here = parent


def refuse():
    print("demo agent: refusing to touch %s, it is not a thread's worktree; start a thread from "
          "the page (N) or `container-map thread new`" % os.getcwd(), flush=True)
    sys.exit(1)


def work(prompt):
    if not in_thread_worktree():
        refuse()
    say("demo agent (scripted, no model): reading the task")
    if os.path.isfile("report.py"):
        say("> read report.py: preparing a short description")
        # Bounded and interruptible; this task lets a newcomer see a ship working on first open.
        for step in range(120):
            say("> writing the folder report: step %d of 120" % (step + 1))
            time.sleep(5)
        write("report.py", '\"\"\"A small folder report.\"\"\"\n' + read("report.py"))
        return "added a short description to report.py."
    if os.path.isfile("todo.py"):
        src = read("todo.py")
        say("> read todo.py, test_todo.py")
        if "def complete" not in src:
            write("todo.py", src + COMPLETE)
            say("> edit todo.py: add complete(items, n)")
            return "added todo.complete(items, n). Run the tests to see if it holds."
        if "verify command failed" in prompt and "left = n - 1" in src:
            say("> the verify output says one item too few was marked: the counter starts at n - 1")
            write("todo.py", src.replace("left = n - 1", "left = n"))
            say("> edit todo.py: start the counter at n")
            return "fixed the off-by-one in todo.complete(); the first n open items are marked."
        return "complete() is already there; nothing more to change."
    if os.path.isfile("README.md") and "TODO:" in read("README.md"):
        say("> read README.md")
        lines = read("README.md").splitlines(keepends=True)
        write("README.md", "".join(SECTION if x.lstrip().startswith("TODO:") else x for x in lines))
        say("> edit README.md: write the Getting started section the TODO asked for")
        return "replaced the TODO line in README.md with a Getting started section."
    say("this scripted agent only knows the demo's two tasks (todo-app and notes-site)")
    sys.exit(1)


def check():
    if not in_thread_worktree():
        refuse()
    say("demo agent (scripted, no model): reviewing the folder")
    if os.path.isfile("test_todo.py"):
        done = subprocess.run([sys.executable, "-m", "unittest", "-q"], capture_output=True,
                              text=True)
        say("> python3 -m unittest -q: exit %d" % done.returncode)
        ok = done.returncode == 0
        why = "the tests pass" if ok else "the tests fail"
    else:
        ok = os.path.isfile("README.md") and "TODO:" not in read("README.md")
        why = "the TODO line is gone" if ok else "there is no README without its TODO line"
    say("review: the change is small and does what the task asked." if ok else "review: not done.")
    say("VERDICT: %s - %s" % ("PASS" if ok else "FAIL", why))
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        sys.exit(check())
    say("done: " + work(sys.stdin.read()))
