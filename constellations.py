"""Private thread findings joined to live metadata; no chat messages are read here."""
import json
import os
import re
from pathlib import Path

import ships


def lane_specs(config=None):
    """Only the person's configured groups; findings can also supply their own lane names."""
    raw = (config or {}).get("constellations", [])
    out = []
    for item in raw if isinstance(raw, list) else []:
        item = {"name": item} if isinstance(item, str) else item
        if not isinstance(item, dict) or not valid_lane(item.get("name")):
            continue
        if any(s["name"] == item["name"] for s in out):
            continue
        out.append({**item, "private": item.get("private") is True})
    return out


def valid_lane(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 100 and not any(ord(c) < 32 for c in value)


SOURCES = {"claude-cli", "codex-tui"}
STATES = {"waiting", "working", "failed", "ready", "snoozed", "settled", "done"}


def valid_source(source):
    return isinstance(source, str) and (source in SOURCES or bool(
        re.fullmatch(r"t3-[A-Za-z0-9][A-Za-z0-9._-]*", source)))


def findings(root, config=None):
    keys = {s.get("key"): s["name"] for s in lane_specs(config) if s.get("key")}
    folder = Path(root, ".container-map", "chat-scan", "findings")
    out, skipped = {}, 0
    if not folder.is_dir():       # the scan makes it; a render never does (a read-only or missing root stays as it is)
        return out, skipped
    for path in sorted(folder.glob("*.json")):
        try:
            with path.open(encoding="utf-8") as handle:
                item = json.load(handle)
                read_at = os.fstat(handle.fileno()).st_mtime
            if isinstance(item, dict) and item.get("lane") in keys:
                item["lane"] = keys[item["lane"]]
            source = item.get("source") if isinstance(item, dict) else None
            ident = item.get("id", "") if isinstance(item, dict) else ""
            pending = item.get("unfinished", []) if isinstance(item, dict) else []
            if (not valid_source(source) or not isinstance(ident, str)
                    or not ident.startswith(source + ":")
                    or not ships._thread_id(ident[len(source) + 1:])
                    or not valid_lane(item.get("lane")) or not isinstance(pending, list)
                    or any(not isinstance(p, dict) or any(
                        not isinstance(p.get(k, ""), str) for k in ("what", "evidence", "when", "owes"))
                           for p in pending)):
                raise ValueError("unknown finding schema")
            item["_read_at"] = read_at
            out[ident] = item
        except (OSError, ValueError, TypeError):
            skipped += 1
    return out, skipped


def lane_for(title, path="", config=None):
    specs = lane_specs(config)
    order = (config or {}).get("constellation_rules", [])
    if isinstance(order, list):
        specs = sorted(specs, key=lambda s: order.index(s["name"]) if s["name"] in order else len(order))
    text = (str(title) + " " + str(path)).lower()
    for spec in specs:
        needles = spec.get("keywords", [])
        if isinstance(needles, list) and any(isinstance(n, str) and n and n.lower() in text for n in needles):
            return spec["name"]
    return next((s["name"] for s in specs if s.get("fallback")), None)


def build(root, candidates, now, config=None):
    scanned, skipped = findings(root, config)
    specs = lane_specs(config)
    records, aliases = {}, {}
    for c in candidates:
        facts = c.get("t3", {})
        tid = facts.get("id") or c["uid"][len("ship:t3:"):]
        machine = c.get("machine", "local-wsl")
        source = "t3-" + machine
        if machine in ("local-wsl", "local-windows") and source + ":" + tid not in scanned:
            matches = [f["source"] for f in scanned.values()
                       if f["source"].startswith("t3-") and f["id"].endswith(":" + tid)]
            if len(matches) == 1:
                source = matches[0]
                machine = source[3:]
        ident = source + ":" + tid
        aliases[(c.get("machine", "local-wsl"), tid)] = ident
        records[ident] = {"id": ident, "machine": machine, "title": c["name"],
                          "state": facts["state"], "last": c.get("updated_at"),
                          "parent": source + ":" + facts["parent"] if facts.get("parent") else None,
                          "path": c.get("cwd", ""), "running": int(facts["state"] == "working"),
                          "resume": {"kind": "t3", "url": facts.get("open_url")}}
    for c in candidates:
        facts = c.get("t3", {})
        tid = facts.get("id") or c["uid"][len("ship:t3:"):]
        machine = c.get("machine", "local-wsl")
        if facts.get("parent") and (machine, facts["parent"]) in aliases:
            records[aliases[(machine, tid)]]["parent"] = aliases[(machine, facts["parent"])]
    for ident, f in scanned.items():
        if ident not in records:
            kind = "claude" if f["source"] == "claude-cli" else "codex" if f["source"] == "codex-tui" else "t3"
            tid = ident[len(f["source"]) + 1:]
            records[ident] = {"id": ident, "machine": f["source"].removeprefix("t3-"),
                              "title": f.get("title", ""), "state": f.get("state", "ready"),
                              "last": f.get("last_activity"), "path": f.get("project", ""),
                              "parent": None, "running": 0,
                              "resume": {"kind": kind, **({"url": None} if kind == "t3" else {} if tid.startswith("-") else
                                         {"command": kind + " --resume " + tid if kind == "claude"
                                          else "codex resume " + tid})}}
    groups = {s["name"]: {"lane": s["name"], "label": s["name"], "threads": [], "count": 0,
                           **({"private": True} if s["private"] else {})} for s in specs}
    for f in scanned.values():
        lane = f["lane"]
        groups.setdefault(lane, {"lane": lane, "label": lane, "threads": [], "count": 0})
    private_lanes = {s["name"] for s in specs if s["private"]}
    private = set()   # Includes folded children and noise: the phone must not expose their aliases.
    for ident, rec in records.items():
        if scanned.get(ident, {}).get("lane") in private_lanes or lane_for(rec["title"], rec.get("path"), config) in private_lanes:
            private.add(ident)
    folded = {}
    for ident, rec in records.items():
        root_id, seen = ident, {ident}
        while records[root_id].get("parent") in records:
            parent = records[root_id]["parent"]
            if parent in seen:
                break
            seen.add(parent)
            root_id = parent
        folded.setdefault(root_id, []).append(rec)
    for root_id, members in folded.items():
        rec = records[root_id]
        f = scanned.get(root_id, {})
        latest = max((m.get("last") for m in members), key=ships.stamp)
        pending = []
        changed = False
        for member in members:
            finding = scanned.get(member["id"], {})
            last = latest if member["id"] == root_id else member.get("last")
            stale = ships.stamp(last) > finding.get("_read_at", float("inf"))
            changed = changed or stale
            pending.extend({**p, "changed_since_read": stale}
                           for p in finding.get("unfinished", []))
        active = [p for p in pending if not p["changed_since_read"]]
        lane = f.get("lane") or lane_for(rec["title"], rec.get("path"), config)
        if lane in private_lanes:
            private.update(m["id"] for m in members)
        if f.get("opened_by") in ("agent", "assistant") and not f.get("his_msgs") and not pending:
            continue
        running = sum(m["running"] for m in members)
        state = "working" if running else rec["state"]
        if state not in STATES:
            state = "ready"
        row = {"id": root_id, "machine": rec["machine"], "title": str(rec["title"])[:40],
               "state": state, "last": latest,
               "children": max(len(members) - 1, len(f["children"]) if isinstance(f.get("children"), list)
                               else ships._count(f.get("children"))),
               "member_ids": [m["id"] for m in members],
               "running": running, "unfinished_n": len(active), "top": active[0] if active else None,
               "unfinished": pending, "changed_since_read": changed,
               "next_step": "" if ships.stamp(latest) > f.get("_read_at", float("inf"))
                            else str(f.get("next_step") or ""),
               "resume": rec["resume"]}
        if lane is None:
            continue
        groups[lane]["threads"].append(row)
        groups[lane]["count"] += 1
    for group in groups.values():
        group["threads"].sort(key=lambda t: (ships.stamp(t["last"]), t["id"]))
    for lane in private_lanes:
        groups[lane]["private_ids"] = sorted(private)
        groups[lane]["private_titles"] = sorted({str(records[i]["title"]) for i in private if records[i].get("title")})
    return list(groups.values()), skipped


def phone(groups):
    """Strict allow-list: private groups carry only their aggregate, others metadata only."""
    out = []
    for group in groups:
        lane = {"lane": group["lane"], "label": group["label"], "count": group.get("count", 0)}
        if not group.get("private"):
            lane["threads"] = [{k: t[k] for k in ("id", "title", "state", "children", "running", "unfinished_n")
                                if k in t} for t in group.get("threads", [])]
        out.append(lane)
    return out
