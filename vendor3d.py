#!/usr/bin/env python3
"""vendor3d.py — the 3D view's two scripts, each named by its content so a browser may keep it for good:

  three-r180-<sha8>.js        vendor/three-r180.js, three.js merged into one classic script (module imports and exports combined)
  cm3d-<skin>-<sha8>.js       the map's own world3d/*.js joined in ORDER (core first), then that skin's 3D data
                              (skins/<skin>-3d.json, or the same name in the config's skins_dir) as CM3D.skin3d,
                              and the neutral file (public) as CM3D.neutral3d, the overlay's fall-through.
                              One skin's data rides only in its own bundle: the neutral bundle never holds another
                              skin's shapes, colours or numbers.

  xterm-6-<sha8>.js           vendor/xterm-6.0.0.js plus its stylesheet (xterm_text), the seat panel's live terminal;
                              served by the bridge only (a page on disk has no terminal), named in page_refs()["xterm"]

The page loads the first two on the first entry into 3D: from the bridge's /vendor/<name> address (immutable, its own gzip),
or, for a page rendered to disk, from the folder ASSET_DIR beside it (write_beside), or out of the page itself when
the config says three_inline (inline_holders: two text/plain holders the page turns into nonced scripts).

  ORDER                                   the module files, core first (it holds the registry the others register on)
  bundle(root, skin, skins_dir)           the joined text, the skin's 3D data (then the neutral file under it) last
  digest(text)                            its first 8 hex digits of sha256
  bundle_name(text, skin)                 cm3d-<skin>-<sha8>.js
  three_text(root) / three_name(text)     the merged three.js and three-r180-<sha8>.js
  assets(root, skin, skins_dir)           [(name, text)] three first, then the bundle
  page_refs(...)                          what the page is told: {"three", "bundle", "dir", "inline"}
  write_beside(out, skin, ...)            the two files into <dirname(out)>/ASSET_DIR (only the missing ones)
  serve(name, game_dir, root, skins_dir)  the text the /vendor/<name> address answers, or None (bridge.py)
  serve_map(name, root, skins_dir)        a realistic planet's map for /vendor/cm3dp-<skin>-<sha8>.<ext> (bridge.py)
  inline_holders(...)                     the two holders for a one-file page

  python3 vendor3d.py [--out DIR] [--skin NAME]   print both names and sizes (and write them into DIR)

Stdlib only. A missing module file or a missing three.js file is an error, never a smaller bundle that loads and
then fails where it is used.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ORDER = ("core", "flight", "hullkit", "rocks", "space", "routes", "fx", "craft", "camera", "hud")
SKIN_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,39}\Z")
THREE_FILE = os.path.join("vendor", "three-r180.js")
THREE_STEM = "three-r180"
XTERM_FILE = os.path.join("vendor", "xterm-6.0.0.js")
XTERM_CSS = os.path.join("vendor", "xterm-6.0.0.css")
XTERM_STEM = "xterm-6"
NAME_RE = re.compile(r"\A(?P<kind>three-r180|xterm-6|cm3d-(?P<skin>[a-z0-9][a-z0-9-]{0,39}))-(?P<sha>[0-9a-f]{8})\.js\Z")
ASSET_DIR = "cm3d"            # beside a page rendered to disk: <dir of the page>/cm3d/<name>
# a planet map of a skin's realistic planets: skins/<skin>-planets3d/<sha8>.<ext>, served as
# /vendor/cm3dp-<skin>-<sha8>.<ext>, or beside a static page in ASSET_DIR
MAP_RE = re.compile(r"\Acm3dp-(?P<skin>[a-z0-9][a-z0-9-]{0,39})-(?P<sha>[0-9a-f]{8})\.(?P<ext>webp|jpg)\Z")
MAP_TYPES = {"webp": "image/webp", "jpg": "image/jpeg"}


def files(root: str = HERE) -> list:
    """The module files in bundle order."""
    return [os.path.join(root, "world3d", name + ".js") for name in ORDER]


def skin_ok(skin: str) -> str:
    if not SKIN_RE.match(skin or ""):
        raise ValueError(f"vendor3d: not a skin name: {skin!r}")
    return skin


def skin3d_path(skin: str, root: str = HERE, skins_dir: str = "") -> str | None:
    """skins/<skin>-3d.json: the config's skins_dir first (as skins are found), then the tool's skins/; None when
    the skin has no 3D data."""
    skin_ok(skin)
    for folder in ([os.path.expanduser(skins_dir)] if skins_dir else []) + [os.path.join(root, "skins")]:
        path = os.path.join(folder, f"{skin}-3d.json")
        if os.path.isfile(path):
            return path
    return None


def skin3d(skin: str, root: str = HERE, skins_dir: str = "") -> dict:
    """That skin's 3D data, {} when it has none; a file that is not a JSON object is an error."""
    path = skin3d_path(skin, root, skins_dir)
    if path is None:
        return {}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"vendor3d: {path} is not a JSON object")
    ship = data.get("ship3d")
    if isinstance(ship, str):
        # the baked owner's ship rides in the bundle in place of its file name; a name that
        # is not a plain file beside the skin, or a file that is gone, leaves the hull kit's ship
        side = os.path.join(os.path.dirname(path), ship)
        if os.path.basename(ship) == ship and os.path.isfile(side):
            with open(side, encoding="utf-8") as handle:
                data = dict(data, ship3d=json.load(handle))
        else:
            data = {k: v for k, v in data.items() if k != "ship3d"}
    maps = data.get("planets3d")
    if isinstance(maps, str):
        # the realistic planets: the manifest rides in the bundle with each map named as the
        # bridge serves it; a manifest that is gone, or a name that is not a plain file beside the skin, leaves the
        # painted planets
        side = os.path.join(os.path.dirname(path), maps)
        data = {k: v for k, v in data.items() if k != "planets3d"}
        if os.path.basename(maps) == maps and os.path.isfile(side):
            with open(side, encoding="utf-8") as handle:
                found = planets_manifest(json.load(handle), skin)
            if found:
                data["planets3d"] = found
    return data


def planets_manifest(man, skin: str) -> dict | None:
    """The baked planets' manifest as the bundle carries it: sky and each planet's maps as served names, its face size
    and look as baked (the bake's own notes, name, index, kind and world, stay out of the page); None when it is not
    one."""
    if not isinstance(man, dict):
        return None
    ok = lambda v: isinstance(v, str) and re.fullmatch(r"[0-9a-f]{8}\.(webp|jpg)", v)
    served = lambda v: f"cm3dp-{skin}-{v}" if ok(v) else None
    if not served(man.get("sky")):
        return None
    def planet(p):
        if isinstance(p, dict) and served(p.get("a")) and isinstance(p.get("look"), dict):
            face = p["face"] if type(p.get("face")) is int else None
            return {"a": served(p["a"]), "n": served(p.get("n")), "face": face, "look": p["look"]}
        return None
    exact = man.get("planets") if isinstance(man.get("planets"), dict) else {}
    planets = {uid: got for uid, p in exact.items() if (got := planet(p))}
    kinds = {}
    library = man.get("kinds") if isinstance(man.get("kinds"), dict) else {}
    for kind, variants in library.items():
        if isinstance(variants, list):
            kept = [got for p in variants if (got := planet(p))]
            if kept:
                kinds[kind] = kept
    if not planets and not kinds:
        return None
    out = {"sky": served(man["sky"])}
    if planets:
        out["planets"] = planets
    if kinds:
        out["kinds"] = kinds
        out["aliases"] = {k: v for k, v in (man.get("aliases") or {}).items() if v in kinds}
    return out


def serve_map(name: str, root: str = HERE, skins_dir: str = "") -> tuple[bytes, str] | None:
    """(bytes, media type) for /vendor/cm3dp-<skin>-<sha8>.<ext>, or None: only a file of that skin's planet folder
    whose content still has that digest is ever read."""
    found = MAP_RE.match(name or "")
    if not found:
        return None
    try:
        path = skin3d_path(found.group("skin"), root, skins_dir)
    except ValueError:
        return None
    if path is None:
        return None
    sha, ext = found.group("sha"), found.group("ext")
    file = os.path.join(os.path.dirname(path), f"{found.group('skin')}-planets3d", f"{sha}.{ext}")
    try:
        with open(file, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    if hashlib.sha256(data).hexdigest()[:8] != sha:
        return None
    return data, MAP_TYPES[ext]


def script_json(value) -> str:
    """JSON a classic script can hold: no '</' (it could close the element) and no U+2028/2029 (ASCII only)."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")


def bundle(root: str = HERE, skin: str = "neutral", skins_dir: str = "") -> str:
    """world3d/*.js in ORDER, each under a one-line header, then the skin's 3D data as CM3D.skin3d (frozen)."""
    skin_ok(skin)
    parts = []
    for path in files(root):
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        parts.append(f"/* world3d/{os.path.basename(path)} */\n" + text.rstrip("\n") + "\n")
    data = skin3d(skin, root, skins_dir)
    parts.append(f"/* the {skin} skin's 3D data */\n"
                 f"(typeof globalThis!=='undefined'?globalThis:this).CM3D.skin3d=Object.freeze({script_json(data)});\n")
    # the neutral file under it (public data): a key the skin lacks falls through to it at run time
    under = "CM3D.skin3d" if skin == "neutral" else f"Object.freeze({script_json(skin3d('neutral', root, skins_dir))})"
    parts.append(f"(typeof globalThis!=='undefined'?globalThis:this).CM3D.neutral3d={under};\n")
    # Capture the script's directory at load time: /vendor/ live, ASSET_DIR for a static page.
    parts.append("(function(g){var s=g.document&&g.document.currentScript;"
                 "g.CM3D.mapBase=s&&s.src?new URL('.',s.src).href:(g.CM_BRIDGE&&g.CM_BRIDGE.live?'/vendor/':'cm3d/');"
                 "})(typeof globalThis!=='undefined'?globalThis:this);\n")
    return re.sub(r"(?i)</script", r"<\\/script", "".join(parts))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def bundle_name(text: str, skin: str = "neutral") -> str:
    return f"cm3d-{skin_ok(skin)}-{digest(text)}.js"


def three_text(root: str = HERE) -> str:
    with open(os.path.join(root, THREE_FILE), encoding="utf-8") as handle:
        return handle.read()


def three_name(text: str) -> str:
    return f"{THREE_STEM}-{digest(text)}.js"


def xterm_text(root: str = HERE) -> str:
    """The live terminal's one script: xterm.js as it is (its source-map comment dropped, the map has no map file),
    then a line that puts xterm.css into the page once (the page's CSP allows inline styles, not style files)."""
    with open(os.path.join(root, XTERM_FILE), encoding="utf-8") as handle:
        code = handle.read()
    with open(os.path.join(root, XTERM_CSS), encoding="utf-8") as handle:
        css = handle.read()
    code = re.sub(r"\n//# sourceMappingURL=\S*\s*\Z", "\n", code)
    return (code + "/* css/xterm.css */\n(function(d){if(!d||d.getElementById('xterm-css'))return;"
            "var s=d.createElement('style');s.id='xterm-css';s.textContent=" + script_json(css) +
            ";d.head.appendChild(s);})(typeof document!=='undefined'?document:null);\n")


def xterm_name(text: str) -> str:
    return f"{XTERM_STEM}-{digest(text)}.js"


def assets(root: str = HERE, skin: str = "neutral", skins_dir: str = "") -> list:
    three = three_text(root)
    code = bundle(root, skin, skins_dir)
    return [(three_name(three), three), (bundle_name(code, skin), code)]


def page_refs(root: str = HERE, skin: str = "neutral", skins_dir: str = "", inline: bool = False) -> dict:
    (three, _), (code, _) = assets(root, skin, skins_dir)
    refs = {"three": three, "bundle": code, "dir": ASSET_DIR + "/", "inline": bool(inline)}
    try:
        refs["xterm"] = xterm_name(xterm_text(root))   # served by the bridge only (a page on disk has no terminal)
    except OSError:
        pass                                           # no live terminal: the seat panel keeps its snapshot
    return refs


def _write_new(path: str, text: str) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)


def write_beside(out_path: str, skin: str = "neutral", root: str = HERE, skins_dir: str = "") -> list:
    """Both files into <dirname(out_path)>/ASSET_DIR, each only when it is not there yet (a name is its content).
    Returns the names; the page next to them loads them from there when no bridge serves it."""
    folder = os.path.join(os.path.dirname(os.path.abspath(out_path)), ASSET_DIR)
    os.makedirs(folder, exist_ok=True)
    names = []
    for name, text in assets(root, skin, skins_dir):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            _write_new(path, text)
        names.append(name)
    man = skin3d(skin, root, skins_dir).get("planets3d") or {}
    entries = list((man.get("planets") or {}).values())
    for variants in (man.get("kinds") or {}).values():
        entries.extend(variants)
    maps = {man.get("sky")} | {p.get(k) for p in entries for k in ("a", "n")}
    for name in sorted(m for m in maps if m):
        found = serve_map(name, root, skins_dir)
        if not found:
            raise ValueError(f"vendor3d: missing or changed planet map {name}")
        raw, _mime = found
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            with open(path, "wb") as handle:
                handle.write(raw)
        # file:// cannot fetch images safely for WebGL. A local classic script transports
        # the same encoded bytes, then loadCube decodes an untainted Blob off the main thread.
        wrapper = path + ".js"
        if not os.path.isfile(wrapper):
            _write_new(wrapper, "(CM3D.mapBytes||(CM3D.mapBytes={}))"
                       f"[{script_json(name)}]={script_json(base64.b64encode(raw).decode('ascii'))};\n")
    return names


def serve(name: str, game_dir: str | None = None, root: str = HERE, skins_dir: str = "") -> str | None:
    """The text /vendor/<name> answers, or None. Only a name of the two shapes is looked at; the file beside the
    served page (game_dir/ASSET_DIR) is used when its content still has that digest, else the tool's own sources
    are built and used only when they give exactly that name. Nothing else is ever read."""
    found = NAME_RE.match(name or "")
    if not found:
        return None
    if game_dir:
        path = os.path.join(game_dir, ASSET_DIR, name)
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except (OSError, UnicodeDecodeError):
            text = None
        if text is not None and digest(text) == found.group("sha"):
            return text
    try:
        if found.group("kind") == THREE_STEM:
            text = three_text(root)
        elif found.group("kind") == XTERM_STEM:
            text = xterm_text(root)
        else:
            text = bundle(root, found.group("skin"), skins_dir)
    except (OSError, ValueError):
        return None
    return text if digest(text) == found.group("sha") else None


def inline_holders(root: str = HERE, skin: str = "neutral", skins_dir: str = "") -> str:
    """The two scripts as inert text/plain holders, for a page that must be one file (three_inline). The page
    copies each into a nonced <script> on the first entry. Neither can close its element."""
    (_, three), (_, code) = assets(root, skin, skins_dir)
    hold = lambda ident, text: (f'<script type="text/plain" id="{ident}">'
                                + re.sub(r"(?i)</script", r"<\\/script", text) + "</script>")
    return hold("cm3d-three", three) + hold("cm3d-bundle", code)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="the 3D view's two scripts: names, sizes, and optionally the files")
    ap.add_argument("--out", help="write both into this folder")
    ap.add_argument("--skin", default="neutral")
    args = ap.parse_args(argv)
    try:
        pairs = assets(HERE, args.skin)
    except (OSError, ValueError) as exc:
        print(f"vendor3d: {exc}", file=sys.stderr)
        return 1
    for name, text in pairs:
        if args.out:
            os.makedirs(args.out, exist_ok=True)
            _write_new(os.path.join(args.out, name), text)
        print(f"{name}  {len(text.encode('utf-8'))} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
