#!/usr/bin/env python3
"""tools/bake_sprites.py — the 2D craft drawn from the 3D hulls, and generated rocks.

  python3 -I tools/bake_sprites.py [--skin NAME|all] [--check]
      Rasterise each hull of skins/<NAME>-3d.json top down into the letter-grid sprites of skins/<NAME>.json (pilot,
      pilot_boost, carrier, raider, drone), so the 2D map draws the very ship the 3D view flies. The hulls are built by
      world3d/hullkit.js itself (run under node), so there is one geometry. Each model's "sprite" block in the 3D file
      names its grid and size and the letters for each material: [lit, mid, shadow]. Light comes from the nose-left side
      at 35 degrees (a 3-band toon ramp: the face's light term, then a highlight on edges facing the light and a shadow on
      edges facing away), emissive materials keep one letter, no outline letter is written (the page draws the halo),
      and a part marked "bake": false (a 3D-only light strip inside the hull's extent) is left out.
      The 2D skin's livery gains the colours of the shade letters the own craft use (the 3D material at that band).
      --check: bake in memory and exit 1 if a skin file differs (the sprites are stale), printing which grids.
  python3 -I tools/bake_sprites.py --rocks [--check]
      Generate three seeded, noise-displaced rocks with closed surfaces at 476, 160 and 60 triangles.
      Every level shares one vertex array; no external mesh or asset folder is needed.

Stdlib only. Needs node for the sprite bake (the rock bake does not).
"""
from __future__ import annotations

import argparse
import colorsys
import random
import json
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKINS = os.path.join(ROOT, "skins")
ROCKS_JS = os.path.join(ROOT, "world3d", "rocks.js")
ROCK_KINDS = ("crater", "grey", "orange")          # the three the neutral build carries, in file order
ROCK_LODS = (476, 160, 60)
ROCK_PX = (56, 24)
# the light the sprites are drawn in: from the nose-left side, 35 degrees up (x right, y up, z back; nose toward -z)
_EL, _AZ = math.radians(35), math.radians(45)
LIGHT = (-math.cos(_EL) * math.sin(_AZ), math.sin(_EL), -math.cos(_EL) * math.cos(_AZ))
BAND_K = (1.06, 0.84, 0.6)                         # lit, mid, shadow: what a band does to the material colour
PAGE_DYNAMIC = frozenset("ae")                     # letters the page fills per craft (themepacks.DYNAMIC_PAGE)


# ------------------------------------------------------------------ node: the hulls, built by world3d/hullkit.js
NODE_DUMP = r"""
const fs = require('fs'), path = require('path');
const [root, skin] = process.argv.slice(1);
globalThis.CM3D = {v: 1, module() {}};
require(path.join(root, 'world3d', 'hullkit.js'));
const HK = globalThis.CM3D.hull;
const read = (n) => { const p = path.join(root, 'skins', n + '.json'); return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : null; };
const neutral = read('neutral-3d'), own = skin === 'neutral' ? {} : (read(skin + '-3d') || {});
const R = HK.resolve(own, neutral), out = {};
for (const [name, rec] of Object.entries(R.models3d || {})) {
  const sprite = rec.sprite || (rec.from && R.models3d[rec.from] ? null : null);
  if (!sprite) continue;
  const b = HK.flightBuilt(HK.build(rec, {lod: 0, models: R.models3d}));
  out[name] = {sprite, mats: b.recipe.mats, bbox: b.stats.bbox, engines: b.anchors.engines,
               tris: b.tris.filter(t => !t.nobake).map(t => [t.p.map(v => Math.round(v * 1e5) / 1e5), t.m])};
}
process.stdout.write(JSON.stringify(out));
"""


def hulls(skin: str) -> dict:
    run = subprocess.run(["node", "-e", NODE_DUMP, ROOT, skin], capture_output=True, text=True, timeout=120)
    if run.returncode:
        raise SystemExit(f"bake_sprites: node could not build the hulls of {skin}: {run.stderr.strip()[-800:]}")
    return json.loads(run.stdout)


# ------------------------------------------------------------------ rasterising
def _normal(p):
    ax, ay, az = p[3] - p[0], p[4] - p[1], p[5] - p[2]
    bx, by, bz = p[6] - p[0], p[7] - p[1], p[8] - p[2]
    n = (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)
    length = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) or 1.0
    return (n[0] / length, n[1] / length, n[2] / length)


def raster(tris, w, h, cell, x0, z0, sub=3):
    """For each cell: the material and normal of the topmost face over it, or None (fewer than 4 of 9 samples hit)."""
    hits = [[[] for _ in range(w)] for _ in range(h)]
    top = {}
    for index, (p, mat) in enumerate(tris):
        xs, zs = (p[0], p[3], p[6]), (p[2], p[5], p[8])
        det = (zs[1] - zs[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (zs[0] - zs[2])
        if abs(det) < 1e-12:
            continue                                           # edge-on from above
        c0 = max(0, int(math.floor((min(xs) - x0) / cell * sub)))
        c1 = min(w * sub - 1, int(math.ceil((max(xs) - x0) / cell * sub)))
        r0 = max(0, int(math.floor((min(zs) - z0) / cell * sub)))
        r1 = min(h * sub - 1, int(math.ceil((max(zs) - z0) / cell * sub)))
        for sr in range(r0, r1 + 1):
            z = z0 + (sr + 0.5) * cell / sub
            for sc in range(c0, c1 + 1):
                x = x0 + (sc + 0.5) * cell / sub
                l1 = ((zs[1] - zs[2]) * (x - xs[2]) + (xs[2] - xs[1]) * (z - zs[2])) / det
                l2 = ((zs[2] - zs[0]) * (x - xs[2]) + (xs[0] - xs[2]) * (z - zs[2])) / det
                l3 = 1 - l1 - l2
                if l1 < -1e-9 or l2 < -1e-9 or l3 < -1e-9:
                    continue
                y = l1 * p[1] + l2 * p[4] + l3 * p[7]
                key = (sr, sc)
                if key not in top or y > top[key][0]:
                    top[key] = (y, index)
    for (sr, sc), (_y, index) in top.items():
        hits[sr // sub][sc // sub].append(index)
    out = [[None] * w for _ in range(h)]
    for r in range(h):
        for c in range(w):
            got = hits[r][c]
            if len(got) * 9 >= 4 * sub * sub:
                best = max(set(got), key=lambda i: (got.count(i), -i))
                out[r][c] = best
    return out


def bake_one(model: dict, name_override=None) -> dict:
    """Grids (and the livery colours they use) for one hull: {grid name: rows}, {letter: hex}."""
    sp = model["sprite"]
    tris = model["tris"]
    (lo, hi) = model["bbox"]
    span, length = hi[0] - lo[0], hi[2] - lo[2]
    w = int(sp["w"])
    if sp.get("h"):
        h = int(sp["h"])
        cell = max(span / w, length / h)
    else:
        cell = span / w
        h = int(math.ceil(length / cell - 1e-9))
    x0 = (lo[0] + hi[0]) / 2 - w * cell / 2
    z0 = (lo[2] + hi[2]) / 2 - h * cell / 2
    grid = raster(tris, w, h, cell, x0, z0)
    mats, letters = model["mats"], sp["letters"]

    def mass(r, c):
        i = grid[r][c] if 0 <= r < h and 0 <= c < w else None
        return None if i is None else tris[i][1]

    # each cell's light score: the face's light term, a highlight on an edge facing the light, a shadow on an edge facing
    # away, and a slight fall toward the far (lower right) corner, so even a flat panel is painted in three tones
    score, cells = {}, {}
    for r in range(h):
        for c in range(w):
            i = grid[r][c]
            if i is None:
                continue
            mat = tris[i][1]
            n = _normal(tris[i][0])
            nl = n[0] * LIGHT[0] + n[1] * LIGHT[1] + n[2] * LIGHT[2]
            lit_edge = mass(r - 1, c) != mat or mass(r, c - 1) != mat
            dark_edge = mass(r + 1, c) != mat or mass(r, c + 1) != mat
            score[(r, c)] = nl + (0.35 if lit_edge else 0) - (0.35 if dark_edge and not lit_edge else 0) - 0.18 * (r / h + c / w)
            cells.setdefault(mat, []).append((r, c))
    # three bands per material: the brightest 30% of its cells lit, the darkest 25% in shadow (a 3-band toon ramp that
    # every mass carries, however flat), the fixed thresholds alone for a mass under 8 cells
    band_of = {}
    for mat, lst in cells.items():
        if len(lst) >= 8:
            order = sorted(lst, key=lambda rc: (-score[rc], rc))
            k_lit, k_dark = int(round(len(order) * 0.30)), int(round(len(order) * 0.25))
            for j, rc in enumerate(order):
                band_of[rc] = 0 if j < k_lit else (2 if j >= len(order) - k_dark else 1)
        else:
            for rc in lst:
                band_of[rc] = 0 if score[rc] >= 0.5 else (1 if score[rc] >= -0.05 else 2)
    rows, used = [], {}
    for r in range(h):
        row = []
        for c in range(w):
            i = grid[r][c]
            if i is None:
                row.append(".")
                continue
            mat = tris[i][1]
            word = letters.get(mat) or letters.get("*") or "m"
            if mats.get(mat, {}).get("emit") or len(word) == 1:
                row.append(word[0])
                used.setdefault(word[0], (mat, 0))
                continue
            band = band_of[(r, c)]
            letter = word[band] if len(word) == 3 else (word[0] if band < 2 else word[1])
            row.append(letter)
            used.setdefault(letter, (mat, band if len(word) == 3 else (0 if band < 2 else 2)))
        rows.append("".join(row))
    grids = {name_override or sp["name"]: rows}
    if sp.get("boost"):
        grids[sp["boost"]] = rows + flare(w, model, x0, cell, sp)
    livery = {}
    for letter, (mat, band) in used.items():
        if letter in PAGE_DYNAMIC:
            continue                                    # status colour and engine: the page's, never a skin's
        rgb = _hex(mats.get(mat, {}).get("c", "#808080"))
        k = 1.0 if mats.get(mat, {}).get("emit") else BAND_K[band]
        livery[letter] = "#%02x%02x%02x" % tuple(max(0, min(255, round(v * k * 255))) for v in rgb)
    return {"grids": grids, "livery": livery}


def flare(w, model, x0, cell, sp) -> list:
    """The boost variant's longer flame under each engine: a white-hot core in light and mid blue (only . B b g)."""
    cols = sorted({int((e[0] - x0) / cell) for e in model.get("engines") or [[0]]})
    n = int(sp.get("flare", 8))
    rows = []
    for k in range(n):
        half = max(0, round((n - k) / n * 2.2))
        row = ["."] * w
        for c in cols:
            for d in range(-half - 1, half + 2):
                if 0 <= c + d < w:
                    ad = abs(d)
                    row[c + d] = "g" if ad <= max(0, half - 1) and k < n - 2 else ("B" if ad <= half else ("b" if k < n // 2 else row[c + d]))
        rows.append("".join(row))
    return rows


def _hex(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def bake_skin(skin: str) -> dict:
    """{grids: {name: rows}, livery: {letter: hex}} of one skin: pilot (+boost) and carrier lend the livery."""
    models = hulls(skin)
    grids, livery = {}, {}
    for name in ("own", "carrier", "raider", "drone"):
        if name not in models:
            continue
        got = bake_one(models[name])
        grids.update(got["grids"])
        if name in ("own", "carrier"):
            for k, v in got["livery"].items():
                livery.setdefault(k, v)
    return {"grids": grids, "livery": livery}


def _sprites_block(grids: dict) -> list:
    """The skin file's "sprites" object in its own layout: one grid row per line."""
    out = ['  "sprites": {']
    names = list(grids)
    for k, name in enumerate(names):
        out.append(f'    {json.dumps(name)}: [')
        rows = grids[name]
        out.extend(f'      {json.dumps(r)}' + ("," if j < len(rows) - 1 else "") for j, r in enumerate(rows))
        out.append("    ]" + ("," if k < len(names) - 1 else ""))
    out.append("  },")
    return out


def write_skin(skin: str, baked: dict, check: bool) -> list:
    """Put the baked grids and livery into skins/<skin>.json, touching only the "livery" line and the "sprites" block
    (the other lanes' keys and the file's layout stay as they are). Returns what changed (or would)."""
    path = os.path.join(SKINS, skin + ".json")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    data = json.loads(text)
    order = ("pilot", "pilot_boost", "carrier", "raider", "drone")
    grids = {k: baked["grids"][k] for k in order if k in baked["grids"]}
    stale = [k for k, v in grids.items() if data.get("sprites", {}).get(k) != v]
    if set(data.get("sprites", {})) - set(grids):
        stale.append("sprites (extra grids)")
    liv = dict(data.get("livery") or {})
    liv.update(baked["livery"])
    if liv != data.get("livery"):
        stale.append("livery")
    if check or not stale:
        return stale
    lines = text.split("\n")
    try:
        li = next(i for i, l in enumerate(lines) if l.startswith('  "livery": '))
        si = next(i for i, l in enumerate(lines) if l == '  "sprites": {')
        se = next(i for i in range(si + 1, len(lines)) if lines[i] in ("  },", "  }"))
    except StopIteration:
        data["sprites"], data["livery"] = grids, liv
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        return stale
    block = _sprites_block(grids)
    if lines[se] == "  }":
        block[-1] = "  }"
    lines[li] = '  "livery": ' + json.dumps(liv, ensure_ascii=False) + ("," if lines[li].rstrip().endswith(",") else "")
    lines[si:se + 1] = block
    new_text = "\n".join(lines)
    if json.loads(new_text)["sprites"] != grids:
        raise SystemExit(f"bake_sprites: rewriting {path} went wrong; nothing written")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(new_text)
    return stale


# ------------------------------------------------------------------ original generated rocks
def rock_mesh(seed: int, crater: bool = False) -> dict:
    """Refine a regular icosahedron by bisecting its longest edge, then displace its surface."""
    phi = (1 + math.sqrt(5)) / 2
    vertices = [(0, a, b * phi) for a in (-1, 1) for b in (-1, 1)]
    vertices += [(a, b * phi, 0) for a in (-1, 1) for b in (-1, 1)]
    vertices += [(a * phi, 0, b) for a in (-1, 1) for b in (-1, 1)]

    def unit(v):
        length = math.sqrt(sum(x * x for x in v))
        return tuple(x / length for x in v)

    vertices = [unit(v) for v in vertices]
    distance = lambda a, b: sum((x - y) ** 2 for x, y in zip(vertices[a], vertices[b]))
    edge = min(distance(0, i) for i in range(1, len(vertices)))
    faces = []
    for a in range(12):
        for b in range(a + 1, 12):
            for c in range(b + 1, 12):
                if all(abs(distance(i, j) - edge) < 1e-8 for i, j in ((a, b), (b, c), (c, a))):
                    u = [vertices[b][i] - vertices[a][i] for i in range(3)]
                    v = [vertices[c][i] - vertices[a][i] for i in range(3)]
                    normal = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
                    faces.append([a, b, c] if sum(x*y for x,y in zip(normal,vertices[a])) > 0 else [a,c,b])
    levels = {}
    for count in reversed(ROCK_LODS):
        while len(faces) < count:
            edges = {tuple(sorted((f[i], f[(i+1)%3]))) for f in faces for i in range(3)}
            a, b = max(sorted(edges), key=lambda e: distance(*e))
            middle = len(vertices)
            vertices.append(unit(tuple((x+y)/2 for x,y in zip(vertices[a],vertices[b]))))
            refined = []
            for f in faces:
                if a in f and b in f:
                    for i in range(3):
                        u, v, w = f[i], f[(i+1)%3], f[(i+2)%3]
                        if {u,v} == {a,b}:
                            refined.extend(([u,middle,w],[middle,v,w]))
                            break
                else:
                    refined.append(f)
            faces = refined
        levels[count] = [f[:] for f in faces]
    rng = random.Random(seed)
    waves = [(unit(tuple(rng.uniform(-1,1) for _ in range(3))), rng.uniform(2,7), rng.uniform(0,math.tau)) for _ in range(5)]
    displaced = []
    for v in vertices:
        radius = 0.9 + sum(0.035 * math.sin(sum(a*b for a,b in zip(v,d))*frequency+phase) for d,frequency,phase in waves)
        if crater:
            radius -= 0.22 * math.exp(-((v[1]-0.55)**2 + (v[2]-0.75)**2) / 0.1)
        displaced.append([round(v[0]*radius*1.08,5),round(v[1]*radius*0.92,5),round(v[2]*radius,5)])
    return {"vertices": displaced, "lods": [{"faces": levels[n], "face_palette_index": [rng.randrange(6) for _ in levels[n]]} for n in ROCK_LODS]}


def bake_rocks() -> str:
    """Original deterministic meshes and palettes, generated entirely from the code above."""
    palettes = {"grey": [72,77,84], "orange": [151,91,48], "crater": [103,93,80]}
    models = {}
    for kind in ROCK_KINDS:
        model = rock_mesh(1709 if kind != "crater" else 3613, kind == "crater")
        model["palette_rgb"] = [[min(255,round(c*k)) for c in palettes[kind]] for k in (0.65,0.8,0.95,1.1,1.25,1.4)]
        models[kind] = model
    payload = json.dumps({"lods": ROCK_LODS, "px": ROCK_PX, "models": models}, separators=(",", ":"))
    return ("// world3d/rocks.js — three generated low-poly rocks with two lighter levels each.\n"
            "// Original container-map geometry; regenerate with python3 tools/bake_sprites.py --rocks.\n"
            "(function (g) {\n  'use strict';\n  if (!g.CM3D) return;\n  var rocks = " + payload + ";\n"
            "  Object.keys(rocks.models).forEach(function (key) { var m = rocks.models[key]; m.faces = m.lods[0].faces; m.face_palette_index = m.lods[0].face_palette_index; });\n"
            "  g.CM3D.rocks = rocks;\n})(typeof globalThis !== 'undefined' ? globalThis : this);\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="bake the 2D craft from the 3D hulls, or generated rocks")
    ap.add_argument("--skin", default="all", help="a skin name (neutral, ...) or all (every skins/<name>-3d.json with a 2D skin)")
    ap.add_argument("--check", action="store_true", help="exit 1 when a written file would change (stale)")
    ap.add_argument("--rocks", action="store_true", help="write world3d/rocks.js instead")
    a = ap.parse_args(argv)
    if a.rocks:
        text = bake_rocks()
        old = open(ROCKS_JS, encoding="utf-8").read() if os.path.exists(ROCKS_JS) else ""
        if a.check:
            print("rocks.js " + ("current" if old == text else "STALE"))
            return 0 if old == text else 1
        with open(ROCKS_JS, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"wrote {ROCKS_JS} ({len(text.encode())} bytes)")
        return 0
    if a.skin == "all":
        names = sorted(n[:-len("-3d.json")] for n in os.listdir(SKINS) if n.endswith("-3d.json")
                       and os.path.exists(os.path.join(SKINS, n[:-len("-3d.json")] + ".json")))
    else:
        names = [a.skin]
    stale_any = False
    for skin in names:
        stale = write_skin(skin, bake_skin(skin), a.check)
        if stale:
            stale_any = True
        print(f"{skin}: " + (("stale: " if a.check else "baked: ") + ", ".join(stale) if stale else "current"))
    return 1 if (a.check and stale_any) else 0


if __name__ == "__main__":
    raise SystemExit(main())
