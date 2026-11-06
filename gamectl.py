#!/usr/bin/env python3
"""Queue bounded game controls through the local bridge; JSON in and JSON out."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def fail(message: str, *, status: int | None = None, detail=None) -> int:
    value = {"ok": False, "error": message}
    if status is not None:
        value["status"] = status
    if detail is not None:
        value["detail"] = detail
    print(json.dumps(value, separators=(",", ":")))
    return 1


def live_file(args, environ=None, cwd=None) -> str:
    """The credentials file this shell means, found the way the CLI and the bridge keep it:
    --live-file, $BRIDGE_LIVE_PATH, else ROOT/.container-map/run/bridge-<port>.json for --root
    (or $CONTAINER_ROOT, or the nearest map above the current folder) and --port (or $BRIDGE_PORT,
    or the one bridge that is set up there), else the old .bridge-live beside the code."""
    if args.live_file:
        return args.live_file
    import config               # late: a plain --live-file call needs none of it
    return config.resolve_live(args.root, args.port, environ, cwd)


def live_config(path: str) -> dict:
    try:
        mode = os.stat(path).st_mode & 0o777
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"bridge metadata unavailable: {exc}") from exc
    if mode & 0o077:
        raise RuntimeError("bridge metadata is not private (expected mode 600)")
    if raw.get("version") != 1 or not isinstance(raw.get("port"), int):
        raise RuntimeError("bridge metadata has an unsupported schema")
    if not isinstance(raw.get("agent_token"), str) or not raw["agent_token"]:
        raise RuntimeError("bridge metadata has no agent control token")
    if not 1 <= raw["port"] <= 65535:
        raise RuntimeError("bridge metadata contains an invalid port")
    return raw


def request(config: dict, method: str, path: str, body=None) -> tuple[int, object]:
    url = f"http://127.0.0.1:{config['port']}{path}"
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    headers = {"Authorization": "Bearer " + config["agent_token"]}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read())
        except ValueError:
            detail = {"error": "bridge returned a non-JSON error"}
        return exc.code, detail
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"bridge request failed: {exc}") from exc


def action_from_args(args) -> dict | None:
    if args.command == "state":
        return None
    if args.command == "move":
        return {"type": "move", "direction": args.direction, "tiles": args.tiles}
    if args.command == "visit":
        return {"type": "visit", "target": args.target}
    if args.command == "compose":
        return {"type": "compose", "target": args.target, "text": args.text}
    return {"type": args.command}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="control the local container-map avatar")
    root.add_argument("--live-file", default="", help="the bridge's credentials file (else "
                      "found from --root and --port, see below)")
    root.add_argument("--root", default="", help="the map's folder (default: $CONTAINER_ROOT, "
                      "else the map above the current folder)")
    root.add_argument("--port", type=int, default=0, help="the bridge's port (default: "
                      "$BRIDGE_PORT, else the one bridge set up for the root)")
    root.add_argument("--request-id", help="idempotency key; generated when omitted")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("state", help="show the bounded control queue and configured targets")
    move = sub.add_parser("move", help="move the avatar a bounded number of tiles")
    move.add_argument("direction", choices=("up", "down", "left", "right"))
    move.add_argument("tiles", nargs="?", type=int, default=1)
    visit = sub.add_parser("visit", help="walk to a WORLD uid")
    visit.add_argument("target")
    compose = sub.add_parser("compose", help="fill (but never submit) a configured seat box")
    compose.add_argument("target", help="seat uid, for example seat:grok")
    compose.add_argument("text", help="one logical line")
    sub.add_parser("dismiss", help="dismiss the open game panel")
    sub.add_parser("stop", help="stop queued avatar movement")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = live_config(live_file(args))
        if args.command == "state":
            status, value = request(config, "GET", "/control")
        else:
            request_id = args.request_id or (args.command + "-" + uuid.uuid4().hex)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", request_id):
                return fail("request id format is invalid")
            status, value = request(config, "POST", "/control",
                                    {"request_id": request_id, "action": action_from_args(args)})
    except RuntimeError as exc:
        return fail(str(exc))
    print(json.dumps(value, separators=(",", ":")))
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
