#!/usr/bin/env python3
"""themepacks.py — themes and skins for the map, shipped as data files.

A THEME is one JSON file, themes/<name>.json: the UI colours, the space background, the colours of
every planet surface, optional 8x8 surface tiles and optional small craft sprites. render-game.py loads
every valid theme at render time and inlines them in the page (it must keep working offline); the page
picks one (config "theme", then the browser's choice) and recolours live.

Theme shape (every colour is #rrggbb):
  {"name", "mood",
   "ui":      {bg, panel, border, text, muted, accent, danger, ok, plate, focus},
   "space":   {"nebula": [3], "stars": [2-4], "scanlines": bool, "glow": 0-1, "base"?: hex},
   "style":   {city, ocean, desert, ice, toxic, blight, gas, rock, belt, star, station (+ aurora, crystal, asteroid)
               -> {a, b, c, d?, glow}},
   "kinds"?:  {code, docs, config, data, media, logs, archive, cache, live, binary, trouble -> {base, land, hi, glow}},
   "sectors"?: {runner, seat, channel, machine, room, code, knowledge, bus, archive, reference, shared, config,
                unknown -> {floor, floor2, grid, accent, glow, ink}},
   "tiles":   {surface -> 8 rows of 8 letters}, "tile_palette": {letter -> hex},
   "sprites": {fighter, cargo, drone (+ probe, beacon, gate) -> rows},
   "sprite_palette": {letter -> hex},
   "planets"?: {purpose -> {colours: [5 hex, dark to light], thresholds: [4 ascending numbers in (0, 1)], glow?: hex}}}
The optional keys exist so the "classic" theme can reproduce the original look exactly: space.base is
the deep-space fill (default ui.bg), space.stars may carry the four star colours, kinds are the
palettes of files by type (derived from style when absent), and sectors recolour the wedges and
plates of the places that are not folders (runner, seat, channel, machine, room; the classic hues when
absent; phosphor uses it to stay one colour). A folder's colours, body, plate and rooms alike, come from
one palette per purpose (the page's PURPOSE: the theme's "planets" block; else, in the classic look, the
skin's or the neutral one; any other theme derives it from its style), so the folder keys of "sectors"
are accepted from older packs and not used. space.glow is
0-1 with the classic look at 0.6. A theme without tiles keeps the procedural planet surfaces; tiles
are repeated on a torus (a texel is two 2 px cells, the tile wraps whole around every planet).
Sprite letters: h m d hull light/mid/dark, c canopy, p P panels, r rotor come from sprite_palette;
a (status), e (engine) and x X y Y (cargo) are filled in by the page per craft, so a grid may use
them without defining them. o (outline), b B (blue), R (red), g (flare), k K (crimson), C (canopy
glass) and T (orange) have page defaults too, so a grid may use them undefined; a theme's
sprite_palette recolours any of them (but a and e).

A SKIN is separate from a theme: one JSON file, skins/<name>.json (or in config "skins_dir", for your
own), chosen by the config key "skin" (default "neutral") and not switchable in the page. It carries
the craft that make the map's characters, the words written on its plates and the wingmate's face:
  {"name", "mood"?,
   "names":   {"carrier", "merged", "worst", "wingmate"}: the carrier's plate, the banner when a thread
              merges (at most 16 characters: the banner is 420 px wide), the word before "-CLASS" on
              the most troubled planet, the wingmate's default name (each 1-24 characters unless
              noted),
   "sprites": {pilot, carrier, raider (+ pilot_boost, drone) -> rows}: the player's craft (and its boost
              variant: the same grid with a longer flare), the hub's own ship (it flies nose right; its
              engine glows sit on the engine cells, e, of its lowest rows) and the problem craft (a drone
              too: the page draws it as foe_drone, never in place of its own small drone). A skin with a
              3D file (skins/<name>-3d.json) has them baked from its hulls by tools/bake_sprites.py.
              (The warp gate is a theme's: a skin does not carry one.)
   "sprite_palette"?: {letter -> hex} for letters of your own (not a or e, the page's status colour
              and engine). The mono palette is fixed, so a skin's own letters do not show under it,
   "livery"?: {letter -> hex}: the colours your own craft (the pilot, the crew and the carrier) wear over
              the theme's craft palette, so they stand out from everything else on the map; only in a
              theme without tiles (the classic look), never in the mono palette, not a or e,
   "portrait"?: {"palette": {letter -> hex}, "frames": {"IDLE", "BLINK"?, "TALK"? -> equal rows}}: the
              wingmate's face, up to 48x48,
   "planets"?: {purpose -> {colours, thresholds, glow?}}: the planet bodies' colours per purpose (code, notes,
              reference, data, media, config, shared, archive, unsorted): five colours from dark to light and four
              ascending thresholds between 0 and 1 (the theme's own "planets" block wins over it; without either the
              page has neutral colours),
   "working_ring"?: hex: the arc that fills around a planet while work runs there (default the page's working cyan)}
A skin's craft grid is 7-49 wide (either parity) with at most 72 rows, drawn nose up; the page draws a wider,
baked grid on smaller cells (craftCell), so a craft keeps its size on the map. The renderer draws a one-pixel dark
halo under every cell, so a grid needs no ring of o cells. Lit from inside (GLOW_LETTERS): e the engine, g the boost
flare, l a lamp.

A 3D SKIN is skins/<name>-3d.json (validate_skin3d): "look3d" (the look, its rim, sun and glow colours),
"feel3d" (the flight table the 3D camera reads, with "meter": {burn > 0, refill > 0, lockout}), "atlas3d"
(the decal atlas: panels of 64-128 px drawn from a few operations), "models3d" (each craft a recipe of hull-kit
parts: loft, slab, fin, cyl, box, quad, mesh, torus; or {"from": another, "lod": 1}) and, private skins only,
"meshes3d" (meshes kept whole, for a look of their own). A skin's 3D file is an overlay: whatever it lacks comes from neutral-3d.json. The
neutral file carries no "PRIVATE" marker, no "meshes3d" block.

The stylesheet keeps its original colours as the "classic" defaults; themed_css() turns every colour
in it into a CSS variable (same default value, so nothing changes), and recolor() computes what
each of those colours becomes in another theme, so one 10-colour palette re-skins the whole UI.
ui.plate is the opaque fill behind the map's name plates (text and muted are written on it) and
ui.focus the keyboard focus ring; quality() holds both to their contrast floors.
"""
from __future__ import annotations

import colorsys
import json
import os
import re

SURFACES = ("city", "ocean", "desert", "ice", "toxic", "blight", "gas", "rock", "belt", "star", "station")
# the purpose bodies' own surfaces (ui/kinds.js PURPOSE: media = aurora, config = crystal, archive = asteroid), optional in "style"
# and "tiles": a theme without them colours those bodies from toxic, ice and belt (kindsSurface), so they never share one palette
SURFACES_OPTIONAL = ("aurora", "crystal", "asteroid")
KINDS = ("code", "docs", "config", "data", "media", "logs", "archive", "cache", "live", "binary", "trouble")
# the nine planet purposes (ui/kinds.js PURPOSE): a skin's or a theme's optional "planets" block colours each one with five
# colours from dark to light and the four thresholds between them (the height field a body's surface is cut by)
PURPOSES = ("code", "notes", "reference", "data", "media", "config", "shared", "archive", "unsorted")
PLANET_FIELDS = ("colours", "thresholds")
PLANET_OPTIONAL = ("glow",)
SECTOR_KEYS = ("runner", "seat", "channel", "machine", "room", "code", "knowledge", "bus", "archive", "reference",
               "shared", "config", "unknown")
SECTOR_FIELDS = ("floor", "floor2", "grid", "accent", "glow", "ink")
UI_KEYS = ("bg", "panel", "border", "text", "muted", "accent", "danger", "ok", "plate", "focus")
SPRITES = ("fighter", "cargo", "drone")
SPRITES_OPTIONAL = ("probe", "beacon")
SPRITES_BIG = ("gate",)                                         # a theme's warp gate (a skin has none)
SPRITES_SKIN = ("pilot", "pilot_boost", "carrier", "raider")    # the characters' craft: skins only
SKIN_FOES = ("drone",)                       # a skin's problem drone (the page draws it as foe_drone; a theme's drone stays)
SKIN_REQUIRED = ("pilot", "carrier", "raider")
CRAFT_WIDTH, CRAFT_ROWS = (7, 33), 41        # a theme's big craft (the warp gate): odd width
SKIN_CRAFT_WIDTH, SKIN_CRAFT_ROWS = (7, 49), 72   # a skin's craft: baked from its 3D hulls, either parity
GLOW_LETTERS = frozenset("egl")              # lit from inside: engine, boost flare, lamp
DYNAMIC_LETTERS = frozenset("aexXyY")        # filled in by the page per craft (status, engine, cargo)
DYNAMIC_PAGE = frozenset("ae")               # status colour and engine: no pack or skin recolours them
PAGE_LETTERS = frozenset("obBRgkKCTwvil")    # the page's own defaults for the big craft (CRAFT_INK)
HULL_LETTERS = frozenset("hmdcrpP")          # the page's own hull colours (hullPal), recoloured by a theme
SKIN_WORDS = ("carrier", "merged", "worst", "wingmate")
WORD_MAX = {"carrier": 24, "merged": 16, "worst": 24, "wingmate": 24}   # the merge banner: 420 px
WORD_RE = re.compile(r"[^\x00-\x1f\x7f<>&\"\\]+\Z")
# a skin's "sky" may name the two banners (a contact resolved, none left); states are said in plain words in every
# skin (words.py), so an older skin's state words there or in "radio" are tolerated and ignored, never an error
SKY_BANNERS = {"cleared": 16, "empty": 16}
PORTRAIT_FRAMES = ("IDLE", "BLINK", "TALK")
PORTRAIT_MAX = 48
NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,23}\Z")
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\Z")
# Ids an install used before a rename. legacy-ids.json beside this file (or the file the environment variable
# CONTAINER_LEGACY_IDS names) maps old to new: {"themes": {old: new}, "surfaces": {old: new}}. A config or a browser's saved
# choice that names an old theme id still lands on the renamed theme; a theme file written with an old surface name still
# loads, with a warning, its style and tile keys renamed. A build ships no such file (a fresh install never had an old id), so
# nothing that is shipped, and no page rendered from it, carries an older release's names.
LEGACY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "legacy-ids.json")


def load_legacy(path=None):
    """(theme aliases, surface aliases) from the legacy-ids file: old id -> new id, both plain names; a surface alias must
    also name a surface. Empty when there is no file, it is unreadable or it holds anything else."""
    path = path or os.environ.get("CONTAINER_LEGACY_IDS") or LEGACY_FILE
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return {}, {}
    if not isinstance(raw, dict):
        return {}, {}

    def table(key, allowed):
        found = raw.get(key)
        if not isinstance(found, dict):
            return {}
        return {old: new for old, new in found.items() if isinstance(new, str) and NAME_RE.match(old)
                and NAME_RE.match(new) and (allowed is None or new in allowed)}
    return table("themes", None), table("surfaces", SURFACES)


THEME_ALIASES, SURFACE_ALIASES = load_legacy()
TILE = 8

# The stylesheet's own colours. Roles are picked from the final cascade of render-game.py's CSS: the
# page background, the panel fill, the frame line, body text, labels, the amber heading colour, the
# stale/dead red, the working green, the name-plate fill and the focus ring. themes/classic.json must
# carry exactly these (a test checks).
UI_BASE = {"bg": "#080d20", "panel": "#0b1428", "border": "#344c6b", "text": "#c8dcf5", "muted": "#9bacc0",
           "accent": "#ffd166", "danger": "#f85149", "ok": "#3dff9e", "plate": "#090f22", "focus": "#ffe28a"}
LINK_BASE = "#5fb4ff"                       # the stylesheet's blue (links, hover frames): the "link" family
RAMP = ("bg", "panel", "border", "muted", "text")

_ROLE_OF = {v: k for k, v in UI_BASE.items()}
# --accent is already a per-panel custom property the page sets inline (the sector colour), so the
# stylesheet must not read the role variable by that name: amber keeps a --c- variable of its own.
_ROLE_VAR_FREE = ("accent",)
# the mono palette (the GB toggle) is fixed whatever the theme: its rules are left alone
_GB_SELECTORS = ("body.gb", "#theme button.on")
_HEX_TOKEN = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_RULE = re.compile(r"([^{}]*)\{([^{}]*)\}")


# ---------------------------------------------------------------- colour helpers

def rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def to_hex(c) -> str:
    return "#" + "".join(f"{max(0, min(255, int(round(v)))):02x}" for v in c)


def mix(a: str, b: str, t: float) -> str:
    x, y = rgb(a), rgb(b)
    return to_hex([x[i] + (y[i] - x[i]) * t for i in range(3)])


def luminance(h: str) -> float:
    """WCAG relative luminance."""
    def f(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb(h)
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def distance(a: str, b: str) -> float:
    x, y = rgb(a), rgb(b)
    return sum((x[i] - y[i]) ** 2 for i in range(3)) ** 0.5


def _hls(h: str) -> tuple[float, float, float]:
    r, g, b = (v / 255 for v in rgb(h))
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    return hue * 360, light, sat


def _from_hls(hue: float, light: float, sat: float) -> str:
    r, g, b = colorsys.hls_to_rgb((hue % 360) / 360, max(0.0, min(1.0, light)), max(0.0, min(1.0, sat)))
    return to_hex((r * 255, g * 255, b * 255))


# ---------------------------------------------------------------- validation

def _is_hex(v) -> bool:
    return isinstance(v, str) and bool(HEX_RE.match(v))


def _hex_list(v, lo: int, hi: int) -> bool:
    return isinstance(v, list) and lo <= len(v) <= hi and all(_is_hex(x) for x in v)


def _check_rows(rows, label: str, errs: list, palette: dict, width=None, odd_between=None, max_rows=16, any_parity=False) -> None:
    if not isinstance(rows, list) or not rows or not all(isinstance(r, str) and r for r in rows):
        errs.append(f"{label}: needs a list of non-empty strings")
        return
    w = len(rows[0])
    if any(len(r) != w for r in rows):
        errs.append(f"{label}: rows differ in length")
        return
    if width is not None and (w != width or len(rows) != width):
        errs.append(f"{label}: must be {width}x{width}, got {w}x{len(rows)}")
        return
    if odd_between and not ((any_parity or w % 2 == 1) and odd_between[0] <= w <= odd_between[1] and len(rows) <= max_rows):
        parity = "" if any_parity else "odd and "
        errs.append(f"{label}: width must be {parity}{odd_between[0]}-{odd_between[1]} (got {w}), at most {max_rows} rows")
        return
    for ch in "".join(rows):
        if ch == ".":
            continue
        if ch not in palette and not (odd_between and ch in DYNAMIC_LETTERS | PAGE_LETTERS | HULL_LETTERS):
            errs.append(f"{label}: letter {ch!r} is not in the palette")
            return


def _check_planets(planets, errs: list, label: str = "planets") -> None:
    """A "planets" block: purpose -> {colours: five #rrggbb dark to light, thresholds: four ascending numbers in (0, 1),
    glow?: #rrggbb}. Any subset of the purposes; one entry per purpose."""
    if not isinstance(planets, dict) or not planets or not set(planets) <= set(PURPOSES):
        errs.append(f"{label}: an object with any of " + ", ".join(PURPOSES))
        return
    for k, v in planets.items():
        why = ""
        if not (isinstance(v, dict) and set(PLANET_FIELDS) <= set(v) <= set(PLANET_FIELDS + PLANET_OPTIONAL)):
            why = "colours and thresholds (glow optional)"
        elif not _hex_list(v["colours"], 5, 5):
            why = "colours: exactly five #rrggbb, dark to light"
        elif not (isinstance(v["thresholds"], list) and len(v["thresholds"]) == 4
                  and all(isinstance(x, (int, float)) and not isinstance(x, bool) and 0 < x < 1 for x in v["thresholds"])
                  and all(a < b for a, b in zip(v["thresholds"], v["thresholds"][1:]))):
            why = "thresholds: four ascending numbers between 0 and 1"
        elif "glow" in v and not _is_hex(v["glow"]):
            why = "glow: #rrggbb"
        if why:
            errs.append(f"{label}.{k}: {why}")


def validate(pack, stem: str | None = None) -> list[str]:
    """What is wrong with a pack, as a list of sentences; [] = valid."""
    errs: list[str] = []
    if not isinstance(pack, dict):
        return ["a pack is a JSON object"]
    name = pack.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        errs.append("name: lowercase letters, digits, - and _, up to 24")
    elif stem is not None and name != stem:
        errs.append(f"name {name!r} must match the file name {stem!r}")
    if not (isinstance(pack.get("mood"), str) and 0 < len(pack["mood"]) <= 200):
        errs.append("mood: one line of text")
    ui = pack.get("ui")
    if not isinstance(ui, dict) or set(ui) != set(UI_KEYS) or not all(_is_hex(v) for v in ui.values()):
        errs.append("ui: exactly " + ", ".join(UI_KEYS) + ", each #rrggbb")
    sp = pack.get("space")
    if not isinstance(sp, dict):
        errs.append("space: an object")
    else:
        if not _hex_list(sp.get("nebula"), 3, 3):
            errs.append("space.nebula: three #rrggbb")
        if not _hex_list(sp.get("stars"), 2, 4):
            errs.append("space.stars: two to four #rrggbb")
        if not isinstance(sp.get("scanlines"), bool):
            errs.append("space.scanlines: true or false")
        g = sp.get("glow")
        if isinstance(g, bool) or not isinstance(g, (int, float)) or not 0 <= g <= 1:
            errs.append("space.glow: a number from 0 to 1")
        if "base" in sp and not _is_hex(sp["base"]):
            errs.append("space.base: #rrggbb")
    style = pack.get("style")
    if not isinstance(style, dict) or not set(SURFACES) <= set(style) <= set(SURFACES + SURFACES_OPTIONAL):
        errs.append("style: exactly " + ", ".join(SURFACES) + " (" + ", ".join(SURFACES_OPTIONAL) + " optional)")
    else:
        for k, v in style.items():
            ok = (isinstance(v, dict) and {"a", "b", "c", "glow"} <= set(v) <= {"a", "b", "c", "d", "glow"}
                  and all(_is_hex(x) for x in v.values()))
            if not ok:
                errs.append(f"style.{k}: a, b, c, glow (d optional), each #rrggbb")
    if "kinds" in pack:
        kinds = pack["kinds"]
        if not (isinstance(kinds, dict) and set(kinds) == set(KINDS) and all(
                isinstance(v, dict) and set(v) == {"base", "land", "hi", "glow"} and all(_is_hex(x) for x in v.values())
                for v in kinds.values())):
            errs.append("kinds: exactly " + ", ".join(KINDS) + ", each {base, land, hi, glow}")
    if "render" in pack and pack["render"] not in ("pixel", "vector"):
        errs.append("render: \"pixel\" (the default) or \"vector\" (the map is drawn as hairline vectors)")
    if "sectors" in pack:
        sec = pack["sectors"]
        if not (isinstance(sec, dict) and sec and set(sec) <= set(SECTOR_KEYS) and all(
                isinstance(v, dict) and set(v) == set(SECTOR_FIELDS) and all(_is_hex(x) for x in v.values())
                for v in sec.values())):
            errs.append("sectors: any of " + ", ".join(SECTOR_KEYS) + ", each {" + ", ".join(SECTOR_FIELDS) + "}")
    tiles, tpal = pack.get("tiles", {}), pack.get("tile_palette", {})
    if not isinstance(tiles, dict) or not isinstance(tpal, dict):
        errs.append("tiles and tile_palette: objects")
    else:
        if tiles and not (2 <= len(tpal) <= 8):
            errs.append("tile_palette: two to eight letters")
        if not all(isinstance(k, str) and len(k) == 1 and k != "." and _is_hex(v) for k, v in tpal.items()):
            errs.append("tile_palette: one-letter keys, #rrggbb values")
        for k, rows in tiles.items():
            if k not in SURFACES + SURFACES_OPTIONAL:
                errs.append(f"tiles.{k}: not a surface ({', '.join(SURFACES + SURFACES_OPTIONAL)})")
                continue
            _check_rows(rows, f"tiles.{k}", errs, tpal, width=TILE)
            if isinstance(rows, list) and len(set("".join(r for r in rows if isinstance(r, str)))) < 2:
                errs.append(f"tiles.{k}: a flat tile is not a texture")
    if "planets" in pack:
        _check_planets(pack["planets"], errs)
    sprites, spal = pack.get("sprites", {}), pack.get("sprite_palette", {})
    if not isinstance(sprites, dict) or not isinstance(spal, dict):
        errs.append("sprites and sprite_palette: objects")
    else:
        if not all(isinstance(k, str) and len(k) == 1 and k != "." and _is_hex(v) for k, v in spal.items()):
            errs.append("sprite_palette: one-letter keys, #rrggbb values")
        if sprites and not set(SPRITES) <= set(sprites):
            errs.append("sprites: fighter, cargo and drone are all needed")
        allowed = SPRITES + SPRITES_OPTIONAL + SPRITES_BIG
        for k, rows in sprites.items():
            if k in SPRITES_SKIN:
                errs.append(f"sprites.{k}: the characters' craft belong to a skin (skins/<name>.json), not a theme")
                continue
            if k not in allowed:
                errs.append(f"sprites.{k}: not a craft ({', '.join(allowed)})")
                continue
            if k in SPRITES_BIG:
                _check_rows(rows, f"sprites.{k}", errs, spal, odd_between=CRAFT_WIDTH, max_rows=CRAFT_ROWS)
            else:
                _check_rows(rows, f"sprites.{k}", errs, spal, odd_between=(7, 13) if k in SPRITES else (3, 13))
    return errs


def _letters(v) -> bool:
    return isinstance(v, dict) and all(isinstance(k, str) and len(k) == 1 and k != "." and _is_hex(c)
                                       for k, c in v.items())


def validate_skin(skin, stem: str | None = None) -> list[str]:
    """What is wrong with a skin, as a list of sentences; [] = valid. Craft grids: 7-49 wide, at most 72 rows,
    letters the page can colour (a skin's craft are baked from its 3D hulls on a finer grid)."""
    if not isinstance(skin, dict):
        return ["a skin is a JSON object"]
    errs: list[str] = []
    name = skin.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        errs.append("name: lowercase letters, digits, - and _, up to 24")
    elif stem is not None and name != stem:
        errs.append(f"name {name!r} must match the file name {stem!r}")
    if "mood" in skin and not (isinstance(skin["mood"], str) and 0 < len(skin["mood"]) <= 200):
        errs.append("mood: one line of text")
    names = skin.get("names")
    if not isinstance(names, dict) or set(names) != set(SKIN_WORDS) or not all(
            isinstance(v, str) and len(v) <= WORD_MAX[k] and WORD_RE.match(v)
            for k, v in names.items()):
        errs.append("names: exactly " + ", ".join(SKIN_WORDS) + ", each 1-24 characters (merged: at "
                    "most " + str(WORD_MAX["merged"]) + ")")
    spal = skin.get("sprite_palette", {})
    if not _letters(spal):
        errs.append("sprite_palette: one-letter keys, #rrggbb values")
        spal = {}
    elif DYNAMIC_PAGE & set(spal):
        errs.append("sprite_palette: a and e are the page's status colour and engine, not a skin's")
    if "livery" in skin:
        if not (_letters(skin["livery"]) and skin["livery"]):
            errs.append("livery: one-letter keys, #rrggbb values")
        elif DYNAMIC_PAGE & set(skin["livery"]):
            errs.append("livery: a and e are the page's status colour and engine, not a skin's")
    sprites = skin.get("sprites")
    if not isinstance(sprites, dict) or not set(SKIN_REQUIRED) <= set(sprites):
        errs.append("sprites: " + ", ".join(SKIN_REQUIRED) + " are all needed")
    else:
        for k, rows in sprites.items():
            if k not in SPRITES_SKIN + SKIN_FOES:
                errs.append(f"sprites.{k}: not a skin craft ({', '.join(SPRITES_SKIN + SKIN_FOES)})")
                continue
            _check_rows(rows, f"sprites.{k}", errs, spal, odd_between=SKIN_CRAFT_WIDTH, max_rows=SKIN_CRAFT_ROWS,
                        any_parity=True)
    if "portrait" in skin:
        _check_portrait(skin["portrait"], errs)
    for block in ("radio", "sky"):
        if block in skin and not isinstance(skin[block], dict):
            errs.append(f"{block}: an object of words")
    sky = skin.get("sky") if isinstance(skin.get("sky"), dict) else {}
    for key, most in SKY_BANNERS.items():
        v = sky.get(key)
        if v is not None and not (isinstance(v, str) and 0 < len(v) <= most and WORD_RE.match(v)):
            errs.append(f"sky.{key}: plain text, 1-{most} characters")
    if "planets" in skin:
        _check_planets(skin["planets"], errs)
    if "working_ring" in skin and not _is_hex(skin["working_ring"]):
        errs.append("working_ring: #rrggbb (the arc that fills around a planet while work runs there)")
    return errs


# ---------------------------------------------------------------- 3D skins (skins/<name>-3d.json, )
HULL_OPS = ("loft", "slab", "fin", "cyl", "box", "quad", "mesh", "torus")      # world3d/hullkit.js
ATLAS_OPS = ("grid", "lines", "vents", "stripe", "chevron", "rivets", "frame", "text", "checker")
SKIN3D_KEYS = ("name", "PRIVATE", "look3d", "feel3d", "atlas3d", "models3d", "meshes3d", "env3d", "sky3d", "ship3d",
               "planets3d")
# planets3d: the file name of the baked realistic planets' manifest beside the skin,
# <skin>-planets3d.json, its maps in <skin>-planets3d/; the bundle carries the manifest and the bridge the maps
# ship3d: the file name of a baked owner's ship beside the skin, <skin>-ship3d.json; the
# bundle (vendor3d.skin3d) puts that file's data in its place, and the "kit" look flies it instead of the hull kit


def _numbers(v):
    if isinstance(v, bool):
        return
    if isinstance(v, (int, float)):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from _numbers(x)
    elif isinstance(v, list):
        for x in v:
            yield from _numbers(x)


def validate_skin3d(skin3d, stem: str | None = None) -> list[str]:
    """What is wrong with a 3D skin, as a list of sentences; [] = valid. A neutral file (stem neutral-3d) must be
    complete (own, wingman, carrier, raider, drone, beacon models; a feel with a meter) and carry nothing private."""
    if not isinstance(skin3d, dict):
        return ["a 3D skin is a JSON object"]
    errs: list[str] = []
    name = skin3d.get("name")
    if not isinstance(name, str) or not re.match(r"[a-z0-9][a-z0-9_-]{0,20}-3d\Z", name):
        errs.append("name: <skin>-3d, lowercase letters, digits, - and _")
    elif stem is not None and name != stem:
        errs.append(f"name {name!r} must match the file name {stem!r}")
    for k in skin3d:
        if k not in SKIN3D_KEYS:
            errs.append(f"{k}: not a 3D skin key ({', '.join(SKIN3D_KEYS)})")
    neutral = name == "neutral-3d" or stem == "neutral-3d"
    feel = skin3d.get("feel3d")
    if feel is not None or neutral:
        if not isinstance(feel, dict):
            errs.append("feel3d: an object (the flight table)")
        else:
            meter = feel.get("meter")
            if not (isinstance(meter, dict) and "refill" in meter and "burn" in meter):
                errs.append("feel3d.meter: needs burn and refill (seconds to empty, seconds to full) and lockout")
            else:
                for k in ("burn", "refill"):
                    v = meter.get(k)
                    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                        errs.append(f"feel3d.meter.{k}: a number of seconds above 0")
                if "lockout" in meter and not isinstance(meter["lockout"], bool):
                    errs.append("feel3d.meter.lockout: true or false")
            for k, v in feel.items():
                if k not in ("meter", "hudLayout", "chase", "cockpit", "lock", "orbit", "radio", "glow", "help", "speed",
                             "rollRings", "reticle") and (isinstance(v, bool) or not isinstance(v, (int, float))):
                    errs.append(f"feel3d.{k}: a number")
    models = skin3d.get("models3d")
    if models is not None or neutral:
        if not isinstance(models, dict) or not models:
            errs.append("models3d: an object of recipes")
            models = {}
        need = ("own", "wingman", "carrier", "raider", "drone", "beacon") if neutral else ()
        for k in need:
            if k not in models:
                errs.append(f"models3d.{k}: the neutral file needs it (a skin falls back to it)")
        for k, rec in models.items():
            if not isinstance(rec, dict):
                errs.append(f"models3d.{k}: a recipe object")
                continue
            if "from" in rec:
                if rec["from"] not in models and not neutral:
                    pass                                     # resolved against the neutral file's models
                elif rec["from"] not in models or "from" in models.get(rec["from"], {}):
                    errs.append(f"models3d.{k}.from: names a recipe of this file that is not itself a reference")
                continue
            parts, mats = rec.get("parts"), rec.get("mats")
            if not isinstance(parts, list) or not parts:
                errs.append(f"models3d.{k}.parts: a list of hull-kit parts")
                continue
            if not (isinstance(mats, dict) and all(isinstance(m, dict) and _is_hex(m.get("c")) for m in mats.values())):
                errs.append(f"models3d.{k}.mats: material -> {{c: #rrggbb, ...}}")
                mats = {}
            for i, part in enumerate(parts):
                if not isinstance(part, dict) or part.get("op") not in HULL_OPS:
                    errs.append(f"models3d.{k}.parts[{i}]: op must be one of {', '.join(HULL_OPS)}")
                elif mats and part.get("mat") not in mats:
                    errs.append(f"models3d.{k}.parts[{i}]: material {part.get('mat')!r} is not in mats")
            if not any(isinstance(rec.get(a), (int, float)) and rec.get(a) > 0 for a in ("span", "length")):
                errs.append(f"models3d.{k}: span or length (world units) above 0")
    atlas = skin3d.get("atlas3d")
    if atlas is not None:
        panels = atlas.get("panels") if isinstance(atlas, dict) else None
        if not isinstance(panels, list):
            errs.append("atlas3d.panels: a list")
        else:
            for p in panels:
                if not (isinstance(p, dict) and isinstance(p.get("name"), str) and isinstance(p.get("size"), int)
                        and 64 <= p["size"] <= 128):
                    errs.append("atlas3d.panels: each {name, size 64-128, draw}")
                    break
                if any(not isinstance(d, dict) or d.get("op") not in ATLAS_OPS for d in p.get("draw", [])):
                    errs.append(f"atlas3d.{p['name']}: draw operations are {', '.join(ATLAS_OPS)}")
    ship = skin3d.get("ship3d")
    if ship is not None and not (isinstance(ship, str) and re.match(r"[a-z0-9][a-z0-9_-]{0,20}-ship3d\.json\Z", ship)):
        errs.append("ship3d: the file name <skin>-ship3d.json beside the skin")
    maps = skin3d.get("planets3d")
    if maps is not None and not (isinstance(maps, str) and re.match(r"[a-z0-9][a-z0-9_-]{0,20}-planets3d\.json\Z", maps)):
        errs.append("planets3d: the file name <skin>-planets3d.json beside the skin")
    if neutral:
        if "ship3d" in skin3d:
            errs.append("ship3d: a skin's baked ship never rides in the neutral 3D file")
        if "PRIVATE" in skin3d:
            errs.append("PRIVATE: the neutral 3D file carries no private marker")
        if "meshes3d" in skin3d:
            errs.append("meshes3d: a skin's whole meshes never ride in the neutral 3D file")
    return errs


def load_skin3d(name: str, dirs, warn=lambda msg: None) -> dict | None:
    """The 3D skin <name>-3d.json from `dirs` (first valid), else None (a broken file is reported through warn)."""
    for directory in dirs:
        path = os.path.join(directory, name + "-3d.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                skin3d = json.load(handle)
        except (OSError, ValueError) as exc:
            warn(f"3D skin {name}-3d.json unreadable: {exc}")
            continue
        errs = validate_skin3d(skin3d, name + "-3d")
        if errs:
            warn(f"3D skin {name}-3d.json skipped: {errs[0]}")
            continue
        return skin3d
    return None


def _check_portrait(portrait, errs: list) -> None:
    pal = portrait.get("palette") if isinstance(portrait, dict) else None
    frames = portrait.get("frames") if isinstance(portrait, dict) else None
    if not (_letters(pal) and pal and isinstance(frames, dict) and "IDLE" in frames
            and set(frames) <= set(PORTRAIT_FRAMES)):
        errs.append("portrait: {palette: letter -> #rrggbb, frames: IDLE (+ BLINK, TALK)}")
        return
    size = None
    for k in sorted(frames, key=lambda f: f != "IDLE"):
        rows = frames[k]
        _check_rows(rows, f"portrait.{k}", errs, pal)
        if not (isinstance(rows, list) and rows and isinstance(rows[0], str)):
            return
        if size is None:
            size = (len(rows[0]), len(rows))
        elif (len(rows[0]), len(rows)) != size:
            errs.append(f"portrait.{k}: every frame must be the size of the first ({size[0]}x{size[1]})")
        if len(rows[0]) > PORTRAIT_MAX or len(rows) > PORTRAIT_MAX:
            errs.append(f"portrait.{k}: at most {PORTRAIT_MAX}x{PORTRAIT_MAX}")


def quality(pack: dict) -> list[str]:
    """Readability problems in a structurally valid pack (the shipped packs must have none): body
    text 7:1 and secondary text 4.5:1 on the panel and on the plate, the accents against the panel,
    a focus ring that shows (3:1 on bg and panel), a, b and c of every surface apart. (Tiles are
    repeated on a torus, so every 8x8 tile repeats exactly; whether its features are drawn to
    continue across the wrap is the artist's craft, not something a number can judge.)"""
    out: list[str] = []
    ui = pack["ui"]
    if contrast(ui["text"], ui["bg"]) < 4.5 or contrast(ui["text"], ui["panel"]) < 7:
        out.append("ui.text: needs contrast 4.5:1 against bg and 7:1 against panel")
    if contrast(ui["muted"], ui["panel"]) < 4.5:
        out.append("ui.muted: needs contrast 4.5:1 against panel")
    for k in ("accent", "danger", "ok"):
        if contrast(ui[k], ui["panel"]) < 3:
            out.append(f"ui.{k}: needs contrast 3:1 against panel")
    if contrast(ui["text"], ui["plate"]) < 7 or contrast(ui["muted"], ui["plate"]) < 4.5:
        out.append("ui.plate: text needs 7:1 and muted 4.5:1 on it")
    if contrast(ui["focus"], ui["panel"]) < 3 or contrast(ui["focus"], ui["bg"]) < 3:
        out.append("ui.focus: needs contrast 3:1 against bg and panel")
    for k, v in pack["style"].items():
        if min(distance(v["a"], v["b"]), distance(v["a"], v["c"]), distance(v["b"], v["c"])) < 30:
            out.append(f"style.{k}: a, b and c are too close in colour")
    return out


def migrate_surfaces(pack) -> list[str]:
    """Rename a renamed planet surface in a pack's style and tiles (in place); returns the old names
    found. A pack that already has the new name keeps it, and validate() refuses the old key."""
    found: list[str] = []
    if not isinstance(pack, dict):
        return found
    for old, new in SURFACE_ALIASES.items():
        for part in ("style", "tiles"):
            table = pack.get(part)
            if isinstance(table, dict) and old in table and new not in table:
                table[new] = table.pop(old)
                if old not in found:
                    found.append(old)
    return found


def load_dir(directory: str, warn=lambda msg: None) -> dict:
    """Every valid pack in `directory`, keyed by name, classic first then alphabetical. A broken file
    is reported through warn() and skipped: a bad pack must never be a dead page."""
    packs: dict = {}
    try:
        names = sorted(f for f in os.listdir(directory) if f.endswith(".json"))
    except OSError:
        return packs
    for f in names:
        try:
            with open(os.path.join(directory, f), encoding="utf-8") as handle:
                pack = json.load(handle)
        except (OSError, ValueError) as exc:
            warn(f"theme {f} unreadable: {exc}")
            continue
        for old_name in migrate_surfaces(pack):
            warn(f"theme {f}: the surface {old_name!r} is now {SURFACE_ALIASES[old_name]!r}; "
                 "rename it in the file")
        errs = validate(pack, f[:-5])
        if errs:
            warn(f"theme {f} skipped: {errs[0]}")
            continue
        packs[pack["name"]] = pack
    return dict(sorted(packs.items(), key=lambda kv: (kv[0] != "classic", kv[0])))


def resolve_theme(name, packs) -> str:
    """The theme a config or a saved choice means: a pack's own name, or a renamed one's old id;
    "" when there is none (the caller picks its default)."""
    if isinstance(name, str):
        name = THEME_ALIASES.get(name, name)
        if name in packs:
            return name
    return ""


# ---------------------------------------------------------------- skins

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SKIN = "neutral"
DEFAULT_WINGMATE = "Scout"                  # the name when no skin can be loaded at all
DEFAULT_NAMES = {"carrier": "CARRIER", "merged": "THREAD MERGED", "worst": "BLIGHT",
                 "wingmate": DEFAULT_WINGMATE}


def skin_dirs(cfg: dict) -> list[str]:
    """Where skins are looked for: the config's own skins_dir first, then skins/ next to the tool."""
    own = cfg.get("skins_dir")
    out = [os.path.abspath(os.path.expanduser(own))] if isinstance(own, str) and own else []
    return out + [os.path.join(HERE, "skins")]


def load_skin(name: str, dirs, warn=lambda msg: None) -> dict | None:
    """The skin called `name`: the first valid <name>.json in `dirs`, else None. A broken file is
    reported through warn() and skipped."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        return None
    for directory in dirs:
        path = os.path.join(directory, name + ".json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                skin = json.load(handle)
        except (OSError, ValueError) as exc:
            warn(f"skin {name}.json unreadable: {exc}")
            continue
        errs = validate_skin(skin, name)
        if errs:
            warn(f"skin {name}.json skipped: {errs[0]}")
            continue
        return skin
    return None


def active_skin(cfg: dict, warn=lambda msg: None) -> dict | None:
    """The skin the config chooses ("skin", default neutral). One that is missing or broken falls
    back to the neutral skin with a warning; None only when no skin file can be loaded at all."""
    dirs = skin_dirs(cfg)
    want = cfg.get("skin") if isinstance(cfg.get("skin"), str) and cfg.get("skin") else DEFAULT_SKIN
    skin = load_skin(want, dirs, warn)
    if skin is None and want != DEFAULT_SKIN:
        warn(f"skin {want!r} is not a skin in {', '.join(dirs)}; using {DEFAULT_SKIN!r}")
        skin = load_skin(DEFAULT_SKIN, dirs, warn)
    return skin


def skin_names(cfg: dict) -> dict:
    """The words the active skin writes on the map: carrier, merged, worst, wingmate. Anything a
    skin does not give (or no skin at all) is the neutral skin's word."""
    names = ((active_skin(cfg) or {}).get("names")) or {}
    return {k: str(names.get(k) or v) for k, v in DEFAULT_NAMES.items()}


def wingmate_name(cfg: dict) -> str:
    """The wingmate's name when the config gives none: the skin's own, else Scout."""
    return skin_names(cfg)["wingmate"]


# ---------------------------------------------------------------- recolouring the stylesheet

def _family(h: str) -> str | None:
    """Which accent family a stylesheet colour belongs to; None = a neutral of the navy ramp."""
    r, g, b = rgb(h)
    chroma = (max(r, g, b) - min(r, g, b)) / 255
    hue, light, _ = _hls(h)
    if 195 <= hue < 245:                      # the navy family: dark blues are neutrals, bright ones are links
        return "link" if chroma >= 0.25 and light >= 0.45 else None
    if chroma < 0.20:
        return None
    if hue >= 340 or hue < 20:
        return "danger"
    if hue < 75:
        return "accent"
    if hue < 170:
        return "ok"
    return "link"


def _anchor(family: str, ui: dict) -> str:
    return mix(ui["accent"], ui["text"], 0.35) if family == "link" else ui[family]


def recolor(color: str, ui: dict) -> str:
    """What a stylesheet colour (#rrggbb, from the classic look) becomes under another UI palette.
    Role colours map exactly. Neutrals ride the bg -> panel -> border -> muted -> text ramp by their
    lightness (so a light pack flips the dark UI to paper and ink). Saturated colours keep their
    meaning: red follows danger, green follows ok, amber and everything else follow accent, with
    the same lightness offset the original had from its family's base."""
    color = color.lower()
    role = _ROLE_OF.get(color)
    if role:
        return ui[role]
    sign = 1 if luminance(ui["text"]) >= luminance(ui["bg"]) else -1
    fam = _family(color)
    if fam:
        base = LINK_BASE if fam == "link" else UI_BASE[fam]
        hue, light, sat = _hls(_anchor(fam, ui))
        offset = _hls(color)[1] - _hls(base)[1]
        return _from_hls(hue, light + offset * sign, sat)
    light = _hls(color)[1]
    far = "#000000" if sign > 0 else "#ffffff"
    near = "#ffffff" if sign > 0 else "#000000"
    pts = [(0.0, mix(ui["bg"], far, 0.6))]                    # below bg: shadows, toward the far end
    pts += [(_hls(UI_BASE[k])[1], ui[k]) for k in RAMP]
    pts += [(1.0, mix(ui["text"], near, 0.55))]               # above text: highlights
    for (l0, c0), (l1, c1) in zip(pts, pts[1:]):
        if light <= l1:
            return mix(c0, c1, (light - l0) / (l1 - l0) if l1 > l0 else 0)
    return pts[-1][1]


def _expand(token: str) -> str:
    """'#fff' -> 'ffffff'; '#0B1428F5' -> '0b1428f5': the 6 or 8 lowercase digits."""
    t = token[1:].lower()
    return "".join(c * 2 for c in t) if len(t) == 3 else t


def themed_css(css: str) -> tuple[str, list[str], list[str]]:
    """Return (css with every colour in a declaration as a var(), the default value of each
    variable as 'name:#value', the 6/8-digit colours that got a --c-<digits> variable). Roles map to
    --bg --panel --border --text --muted --danger --ok when the colour is exactly that role's
    colour (accent: see _ROLE_VAR_FREE); every other colour gets --c-<digits>, alpha included.
    Rules of the mono palette are left alone."""
    keys: dict[str, None] = {}

    def token(m):
        digits = _expand(m.group(0))
        if len(digits) == 6:
            role = _ROLE_OF.get("#" + digits)
            if role and role not in _ROLE_VAR_FREE:
                return f"var(--{role})"
        keys.setdefault(digits)
        return f"var(--c-{digits})"

    def rule(m):
        selector, body = m.group(1), m.group(2)
        if any(g in selector for g in _GB_SELECTORS):
            return m.group(0)
        return selector + "{" + _HEX_TOKEN.sub(token, body) + "}"

    out = _RULE.sub(rule, css)
    defaults = [f"{k}:{v}" for k, v in (("--" + r, UI_BASE[r]) for r in UI_KEYS if r not in _ROLE_VAR_FREE)]
    defaults += [f"--accent:{UI_BASE['accent']}"]
    defaults += [f"--c-{d}:#{d}" for d in keys]
    return out, defaults, list(keys)


def pack_css(pack: dict, keys: list[str]) -> str:
    """The rule that re-skins the stylesheet for one pack: html[data-theme="name"]{...}. The classic
    look needs none (its variables are the defaults); any pack whose ui equals it needs none either."""
    ui = pack["ui"]
    if all(ui[k].lower() == UI_BASE[k] for k in UI_KEYS):
        return ""
    decls = [f"--{k}:{ui[k]}" for k in UI_KEYS]
    for d in keys:
        decls.append(f"--c-{d}:" + recolor("#" + d[:6], ui) + d[6:])
    return f'html[data-theme="{pack["name"]}"]{{' + ";".join(decls) + "}"


# the page's own rules for the theme line, written with the variables. --accent is also set inline on
# #panel and #side (a sector's colour) and read there with a fallback, var(--accent,#526e8e): the role
# variable on :root must not become that fallback, so those two start from "unset" (initial).
THEME_CSS = (
    "#panel,#side{--accent:initial}"
    "#themeline{font-size:var(--fs-small);letter-spacing:.06em;color:var(--muted);"
    "pointer-events:auto;cursor:pointer}"
    "#themeline b{color:var(--c-ffd166,#ffd166);font-weight:400}"
    "#legend .lg a[data-theme-pick]{cursor:pointer;margin-right:12px;color:var(--c-a8caff,#a8caff);"
    "text-decoration:underline}"
    "#legend .lg a[data-theme-pick].on{color:var(--c-ffd166,#ffd166);text-decoration:none;font-weight:700}"
)


def theme_page(page: str, packs: dict) -> str:
    """The page with its <style> turned into variables, the default values in :root, one rule per
    non-default pack, and the theme line's own CSS. The data-theme attribute and the pack list are
    filled by the caller (placeholders __THEME__, __THEMENAMES__ and __THEMEPACKS__)."""
    head, sep, rest = page.partition("<style>")
    css, close, tail = rest.partition("</style>")
    if not sep or not close:
        return page
    css2, defaults, keys = themed_css(css)
    blocks = "".join(pack_css(p, keys) for p in packs.values())
    root = ":root{" + ";".join(defaults) + "}"
    return head + "<style>" + root + css2 + THEME_CSS + blocks + "</style>" + tail
