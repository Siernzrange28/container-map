#!/usr/bin/env python3
"""cards.py — one place, two readers. The map's planet and folder cards as data: the page shows
them to a person ("for you"), and the bridge serves the very same dict to agents at /agent/unit
and /agent/map ("for agents"), with the calls an agent can make from there. Built from the world
the page already shows plus the task store; no file contents, no secrets (the agent token is
named by the file that holds it, never copied here)."""
from __future__ import annotations

import os
import shlex

BAD = ("STALE", "NEVER", "NO RECEIPT")
LIST_CAP = 60


def _chars(world: dict) -> list[dict]:
    return [c for c in (world.get("chars") or []) if isinstance(c, dict)]


def _inside(path: str, root: str) -> bool:
    return bool(path) and bool(root) and (path == root or path.startswith(root.rstrip("/") + "/"))


def _threads_in(tasks: list[dict], path: str) -> list[dict]:
    out = []
    for t in tasks or []:
        if isinstance(t, dict) and _inside(str(t.get("target") or ""), path):
            c = t.get("check") or {}
            out.append({"id": t.get("id"), "title": t.get("title"), "status": t.get("status"), "agent": t.get("runner"),
                        "model": t.get("model"), "check": c.get("verdict") if isinstance(c, dict) else None})
    return out


def problems(world: dict, tasks: list[dict], unit: dict) -> list[dict]:
    """What is wrong at this planet or folder, each one a sentence an agent can be handed."""
    out, chars = [], _chars(world)
    is_area = unit.get("kind") == "area"
    for c in chars:
        if c.get("kind") != "cron" or c.get("verdict") not in BAD:
            continue
        if (is_area and c.get("area") == unit.get("uid")) or (not is_area and c.get("area") == unit.get("area") and c.get("room") == unit.get("name")):
            out.append({"kind": "cron", "uid": c.get("uid"),
                        "what": f"cron line {c.get('name')} ({c.get('schedule') or '?'}) has no fresh receipt: {str(c.get('receipt') or '').strip()[:140]}".rstrip(": ")})
    items = (world.get("tasks") or {}).get("items") if isinstance(world.get("tasks"), dict) else None
    if is_area:
        for it in items or []:
            if isinstance(it, dict) and it.get("area") == unit.get("name"):
                out.append({"kind": "board", "id": it.get("id"), "what": str(it.get("finding") or it.get("id") or "")[:220]})
    for t in _threads_in(tasks, str(unit.get("path") or "")):
        if t["status"] in ("failed", "lost"):
            out.append({"kind": "thread", "id": t["id"], "what": f"thread “{str(t['title'] or '')[:80]}” {t['status']}"})
    return out


def _agents(runners: list[dict]) -> list[dict]:
    return [{"name": r.get("name"), "label": r.get("label") or r.get("name"), "role": r.get("role") or "both",
             "models": [{"name": m.get("name"), "label": m.get("label") or m.get("name")} for m in r.get("models", [])],
             "default_model": r.get("default_model")} for r in runners]


def _actions(path: str, uid: str, runners: list[dict], agent_verify: bool = False) -> list[dict]:
    workers = [r for r in runners if r.get("role") != "check"]
    r = workers[0] if workers else {}
    model = r.get("default_model")
    body = {"text": "<what to do, specific, with how to verify>", "target": path, "runner": r.get("name") or "<agent>"}
    if agent_verify:                   # the bridge refuses these from an agent unless it is on
        body["verify"] = "<optional: one shell line that must exit 0, e.g. python3 -m unittest -q>"
        body["retries"] = 2
        body["setup"] = "<optional: one shell line run in the fresh worktree before the agent's first run, e.g. npm ci>"
    if model:
        body["model"] = model
    return [
        {"do": "hand it a thread (an agent works here, in its own worktree in a repo"
               + ("; with verify + retries it loops until the command passes)" if agent_verify else ")"),
         "method": "POST", "path": "/agent/task", "body": body},
        {"do": "follow up on a thread", "method": "POST", "path": "/agent/task/followup", "body": {"id": "<thread id>", "text": "<what next>"}},
        {"do": "have another agent check a thread (read-only, ends in VERDICT: PASS|FAIL)", "method": "POST", "path": "/agent/task/check",
         "body": {"id": "<thread id>", "runner": "<a different agent>"}},
        {"do": "read a thread and its log", "method": "GET", "path": "/agent/task?id=<thread id>"},
        {"do": "read this card again", "method": "GET", "path": f"/agent/unit?uid={uid}"},
        {"do": "read the whole map", "method": "GET", "path": "/agent/map"},
        # workflows you can watch: say what you will do, step by step, where each step works; the reply's
        # token (shown once) goes in X-Workflow-Token on every update. `container-map wf schema` is the full spec.
        {"do": "put a workflow on the map: a few steps, each at a folder, a machine or place:<name>; you report "
               "note steps, the map runs task steps through threads, the person passes gates",
         "method": "POST", "path": "/agent/workflow",
         "body": {"title": "<what you are doing, <= 60>", "by": {"name": "<your name>"},
                  "steps": [{"key": "read", "label": "<step, <= 60>", "at": path, "kind": "note"},
                            {"key": "check", "label": "<step>", "at": "place:<a site, an inbox, ...>", "kind": "note",
                             "after": ["read"]},
                            {"key": "ok", "label": "<what the person looks at>", "kind": "gate", "after": ["check"]}]}},
        {"do": "report a step of your workflow (header X-Workflow-Token: the token from the create)",
         "method": "POST", "path": "/agent/workflow/step",
         "body": {"id": "<workflow id>", "key": "<step key>", "state": "working|done|failed|skipped",
                  "says": "<optional, <= 200>", "progress": "<optional, 120/800>",
                  "output": "<optional: a file path on the map, or text>"}},
        {"do": "paint on the map for a while (label, marker, ring, beam, route, radio, fx, card, chart; or a page: "
               "your own HTML, which the person opens in a sandboxed panel): your name is shown on it; it changes "
               "no work",
         "method": "POST", "path": "/agent/show",
         "body": {"by": "<your name>", "at": uid, "ttl": 300,
                  "ops": [{"op": "card", "title": "<= 40", "rows": [["<key>", "<value>"]], "bar": [3, 9]}]}},
        {"do": "read the workflows and the live shows", "method": "GET", "path": "/workflows"},
    ]


def unit_card(world: dict, tasks: list[dict], uid: str, base_url: str = "", tool_dir: str = "", token_file: str = "",
              agent_verify: bool = False) -> dict | None:
    chars = _chars(world)
    u = next((c for c in chars if c.get("uid") == uid and c.get("kind") in ("area", "dir")), None)
    if u is None:
        return None
    path = str(u.get("path") or "")
    by_uid = {c.get("uid"): c for c in chars}
    runners = [r for r in (world.get("task_runners") or []) if isinstance(r, dict)]
    seats = []
    for s in (u.get("seats") or []):
        c = by_uid.get(s) or {}
        seats.append({"uid": s, "name": c.get("alias") or c.get("name") or s, "status": c.get("status")})
    card = {
        "uid": uid, "kind": "planet" if u.get("kind") == "area" else "folder", "name": u.get("name"), "path": path,
        "purpose": u.get("tag_desc") or "", "area_kind": u.get("area_kind"),
        "files": u.get("files"), "size": u.get("size"), "last_write_days": u.get("age"), "life": u.get("life"),
        "instructions_file": (u.get("door") or {}).get("file") if isinstance(u.get("door"), dict) else None,
        "worker": u.get("worker"), "git": u.get("git"), "seats_here": seats,
        "threads_here": _threads_in(tasks, path)[:20],
        "problems": problems(world, tasks, u),
        "trend": (world.get("trend") or {}).get(str(u.get("name"))) if u.get("kind") == "area" else None,
    }
    if u.get("kind") == "area":
        card["inside"] = [{"uid": r.get("uid"), "name": r.get("name"), "path": r.get("path"), "files": r.get("files"), "last_write_days": r.get("age")}
                          for r in (u.get("rooms") or [])[:LIST_CAP] if isinstance(r, dict)]
        card["top_files"] = [{"name": f[0], "path": os.path.join(path, f[0]), "size": f[1], "last_write_days": f[2]}
                             for f in (u.get("root_files") or [])[:LIST_CAP] if isinstance(f, (list, tuple)) and len(f) >= 3]
        card["crons"] = [{"name": c.get("name"), "schedule": c.get("schedule"), "verdict": c.get("verdict")}
                         for c in chars if c.get("kind") == "cron" and c.get("area") == uid][:LIST_CAP]
    else:
        card["inside"] = [{"name": k[0], "files": k[1], "is_folder": bool(k[4] & 1) if len(k) > 4 and isinstance(k[4], int) else None}
                          for k in (u.get("children") or [])[:LIST_CAP] if isinstance(k, (list, tuple)) and k]
        card["planet"] = u.get("area")
    # a planet's name or a folder's path goes to a shell as text, so each part is one quoted word
    door = None
    if tool_dir:
        door_arg = str(u.get("name") if u.get("kind") == "area" else path)
        door = "bash " + shlex.quote(f"{tool_dir}/door.sh") + " " + shlex.quote(door_arg)
    card["commands"] = {"open_a_seat_here": door,
                        "read_a_file": "GET /file?path=<absolute path inside this folder> "
                                       "(same Authorization header; first 64 KB of a text file, no credentials)"}
    card["actions"] = _actions(path, uid, runners, agent_verify)
    card["agents"] = _agents(runners)
    card["auth"] = {"header": "Authorization: Bearer <agent_token>", "token_file": token_file or "<the bridge's live file>",
                    "base_url": base_url, "note": "merging, setting aside and closing threads stay with the person"}
    card["brief"] = brief(card)
    return card


def brief(card: dict) -> str:
    """The card as a short text to paste into any agent."""
    lines = [f"# {card['name']} ({card['kind']}) — {card['path']}"]
    if card.get("purpose"):
        lines.append(f"Purpose: {card['purpose']}")
    facts = [f"{card.get('files')} files" if card.get("files") is not None else None, card.get("size") or None,
             f"last write {card['last_write_days']}d ago" if card.get("last_write_days") is not None else None,
             f"instructions: {card['instructions_file']}" if card.get("instructions_file") else "no instructions file",
             f"git {card['git'].get('branch')} ({card['git'].get('dirty')} dirty)" if isinstance(card.get("git"), dict) else None]
    lines.append("Facts: " + " · ".join(f for f in facts if f))
    if card.get("problems"):
        lines.append("Problems:")
        lines.extend(f"- {p['what']}" for p in card["problems"][:10])
    else:
        lines.append("Problems: none on the map")
    if card.get("threads_here"):
        lines.append("Threads here: " + "; ".join(f"{t['title']} [{t['status']}]" for t in card["threads_here"][:6]))
    if card.get("inside"):
        lines.append("Inside: " + ", ".join(str(r.get("name")) for r in card["inside"][:24]))
    a = card.get("auth") or {}
    lines.append(f"Act through the bridge {a.get('base_url') or ''} with header {a.get('header')} (token in {a.get('token_file')}):")
    lines.extend(f"- {x['method']} {x['path']}  — {x['do']}" for x in card.get("actions", [])[:4])
    return "\n".join(lines)


def map_card(world: dict, tasks: list[dict], base_url: str = "", token_file: str = "", agent_verify: bool = False) -> dict:
    chars = _chars(world)
    planets = []
    for a in chars:
        if a.get("kind") != "area":
            continue
        probs = problems(world, tasks, a)
        working = [s for s in (a.get("seats") or []) if any(c.get("uid") == s and c.get("status") == "WORKING" for c in chars)]
        planets.append({"uid": a.get("uid"), "name": a.get("name"), "path": a.get("path"), "purpose": a.get("tag_desc") or "",
                        "kind": a.get("area_kind"), "files": a.get("files"), "size": a.get("size"), "last_write_days": a.get("age"),
                        "problems": len(probs), "problem_list": [p["what"] for p in probs[:4]],
                        "agents_working": len(working), "trend": ((world.get("trend") or {}).get(str(a.get("name"))) or {}).get("why") or ""})
    runners = [r for r in (world.get("task_runners") or []) if isinstance(r, dict)]
    return {"name": world.get("name"), "root": world.get("root_path"), "built": world.get("generated"),
            "planets": planets,
            "threads": [{"id": t.get("id"), "title": t.get("title"), "status": t.get("status"), "agent": t.get("runner"), "model": t.get("model"),
                         "target": t.get("target"), "check": (t.get("check") or {}).get("verdict") if isinstance(t.get("check"), dict) else None}
                        for t in (tasks or [])[:40] if isinstance(t, dict)],
            "agents": _agents(runners),
            "decisions_waiting": len(world.get("quests") or []),
            "actions": _actions("<absolute folder path from planets[].path>", "<uid from planets[].uid>", runners, agent_verify),
            "auth": {"header": "Authorization: Bearer <agent_token>", "token_file": token_file or "<the bridge's live file>", "base_url": base_url,
                     "note": "merging, setting aside and closing threads stay with the person"}}
