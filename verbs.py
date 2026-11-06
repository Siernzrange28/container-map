#!/usr/bin/env python3
"""verbs.py — verbs are files (slice 6). One JSON manifest per verb in verbs_dir:

  {"name": "git-status", "label": "git status", "applies": ["area", "dir"],
   "cmd": ["git", "status", "--short"], "cwd": "target", "max_minutes": 5,
   "desc": "what is uncommitted here", "text": false}

  {"name": "new-verb", "label": "ask a runner to write a new verb", "applies": ["any"],
   "runner": "*", "text": true, "prompt": "…{text}…{path}…{verbs_dir}…"}

cmd verbs run an argv with {path} {name} {text} substituted (cwd = the target directory, or the
tool dir). runner verbs hand a prompt to a runner ("*" = the first offered runner). The map
offers a verb on every unit whose kind is in `applies` ("any" = everything with a path). Every
run is detached, logged, capped and receipted like a runner. An agent can add a verb by writing
one file; the next render offers it. Invalid manifests are skipped and named in `errors`.

Optional scope="tool" restricts offering AND dispatch to the tool directory. A manifest may
carry more keys (why, test, ...): this loader ignores them and never executes them or exposes
them as page commands.
"""
import json
import math
import os
import re

NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
KINDS = {"any", "area", "dir", "room", "file", "seat", "machine", "star"}


def load(verbs_dir):
    """(verbs: {name: manifest}, errors: [str]) — bounded, sorted, validated."""
    verbs, errors = {}, []
    if not verbs_dir or not os.path.isdir(verbs_dir):
        return verbs, errors
    for fn in sorted(os.listdir(verbs_dir))[:200]:
        if not fn.endswith(".json"):
            continue
        path = os.path.join(verbs_dir, fn)
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as exc:
            errors.append(f"{fn}: unreadable ({exc})")
            continue
        name = raw.get("name") if isinstance(raw, dict) else None
        if not (isinstance(name, str) and NAME_RE.fullmatch(name) and fn == name + ".json"):
            errors.append(f"{fn}: name must match the file name (a-z0-9-)")
            continue
        applies = raw.get("applies") or ["any"]
        if not (isinstance(applies, list) and applies and all(isinstance(a, str) and a in KINDS for a in applies)):
            errors.append(f"{fn}: applies must list unit kinds")
            continue
        cmd, runner = raw.get("cmd"), raw.get("runner")
        if cmd is not None and not (isinstance(cmd, list) and cmd and all(isinstance(a, str) and a for a in cmd)):
            errors.append(f"{fn}: cmd must be an argv list")
            continue
        if runner is not None and not (isinstance(runner, str) and (runner == "*" or NAME_RE.fullmatch(runner))):
            errors.append(f"{fn}: runner must be a runner name or *")
            continue
        if (cmd is None) == (runner is None):
            errors.append(f"{fn}: exactly one of cmd / runner")
            continue
        prompt = raw.get("prompt", "")
        if runner is not None and not (isinstance(prompt, str) and prompt.strip()):
            errors.append(f"{fn}: a runner verb needs a prompt")
            continue
        minutes = raw.get("max_minutes", 10)
        if type(minutes) not in (int, float) or not math.isfinite(minutes) or minutes <= 0 or minutes > 240:
            errors.append(f"{fn}: max_minutes out of range")
            continue
        cwd = raw.get("cwd", "target")
        if cwd not in ("target", "tool"):
            errors.append(f"{fn}: cwd must be target or tool")
            continue
        scope = raw.get("scope", "any")
        if scope not in ("any", "tool"):
            errors.append(f"{fn}: scope must be any or tool")
            continue
        wants_text = bool(raw.get("text")) or any("{text}" in a for a in (cmd or [])) or ("{text}" in prompt)
        verbs[name] = {"name": name, "label": str(raw.get("label") or name)[:60], "applies": applies,
                       "cmd": cmd, "runner": runner, "prompt": prompt, "cwd": cwd, "max_minutes": float(minutes),
                       "desc": str(raw.get("desc", ""))[:300], "text": wants_text, "file": path,
                       "mtime": os.stat(path).st_mtime, "scope": scope}
    return verbs, errors


def public(verbs, errors):
    """What the page needs: no commands, no prompts."""
    return {"items": [{k: v[k] for k in ("name", "label", "applies", "desc", "text", "mtime", "scope")} | {"kind": "runner" if v["runner"] else "cmd"}
                      for v in verbs.values()], "errors": errors[:20]}


def loop_tail(verbs_dir):
    """The most recent local loop receipt, not its prompts or evidence packet."""
    if not verbs_dir:
        return ""
    try:
        path = os.path.join(verbs_dir, "LOOP.md")
        if os.path.islink(path):
            return ""
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 65536))
            lines = handle.read().decode("utf-8", errors="replace").strip().splitlines()
        return lines[-1] if lines else ""
    except OSError:
        return ""
