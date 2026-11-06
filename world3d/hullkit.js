// world3d/hullkit.js — the hull kit: loft, slab, fin, mirror, bevel strips and the decal atlas every craft is built from.
// Registered on the 3D view's registry (world3d/core.js); reads only `world` (cmWorld()) and `ctx`, never page globals.
/* port: nova-lancer (MIT) — the loft/slab/fin/mirrorX hull kit (src/ship.js:11-100), rewritten as plain triangle lists */
//
// A craft is DATA: a recipe in the skin's 3D file (skins/<skin>-3d.json, models3d.<name>) that lists parts, each one of
// the operations below, a material name and an atlas panel. This file holds no craft of its own, so a skin's shapes ride
// only in that skin's bundle. Everything up to toThree() is pure (no THREE, no DOM): node tests and tools/bake_sprites.py
// read the very same triangles the browser draws.
//
//   CM3D.hull.build(recipe, {lod})  -> {tris: [{p:[9 floats], m, part, bevel, atlas}], stats: {tris, bevels, atlasPanels,
//                                       parts, span, length, height}}
//   CM3D.hull.resolve(skin3d, neutral3d) -> the skin's 3D data with every missing model, palette or feel key read from
//                                       the neutral file (a sparse overlay with fallthrough)
//   CM3D.hull.toThree(THREE, built, {colours, atlas, letterCell})  -> BufferGeometry (position, normal, color, uv, aEmit)
//   CM3D.hull.material(THREE, atlasTexture) -> the one hull ShaderMaterial (sun key, toon bands, backlit rim, emissives)
//   CM3D.hull.atlas(THREE, spec) -> {texture, panels: {name: [u0, v0, u1, v1, px]}, letter(ch) -> [u0, v0, u1, v1]}
(function (g) {
  'use strict';
  var CM3D = g.CM3D;
  if (!CM3D) return;
  var HK = {};

  // ---------------------------------------------------------------- vector helpers (plain arrays)
  function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
  function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function norm(a) { var l = Math.sqrt(dot(a, a)) || 1; return [a[0] / l, a[1] / l, a[2] / l]; }
  function triNormal(p) { return norm(cross([p[3] - p[0], p[4] - p[1], p[5] - p[2]], [p[6] - p[0], p[7] - p[1], p[8] - p[2]])); }
  function triArea(p) {
    var c = cross([p[3] - p[0], p[4] - p[1], p[5] - p[2]], [p[6] - p[0], p[7] - p[1], p[8] - p[2]]);
    return Math.sqrt(dot(c, c)) / 2;
  }
  HK.triNormal = triNormal;
  // creased normals for the rim only (the faces stay flat-lit): each corner averages, weighted by the corner's angle, the
  // normals of the triangles meeting at its position that turn less than the crease from its own, so a loft's facets
  // share one smooth normal and the fresnel rim runs as a band along the silhouette, while a wing's edge (a right angle)
  // stays sharp. pos and nrm are flat (9 floats a triangle); returns a new Float32Array like nrm.
  HK.creaseNormals = function (pos, nrm, creaseDeg) {
    var n = pos.length / 9, cosMin = Math.cos((creaseDeg == null ? 66 : creaseDeg) * Math.PI / 180), at = {};
    var out = new Float32Array(nrm.length), q = function (v) { return Math.round(v * 1e3); }, ang = new Float32Array(n * 3);
    for (var t = 0; t < n; t++) {
      for (var v = 0; v < 3; v++) {
        var o = t * 9, i0 = o + v * 3, i1 = o + ((v + 1) % 3) * 3, i2 = o + ((v + 2) % 3) * 3;
        var e1 = norm([pos[i1] - pos[i0], pos[i1 + 1] - pos[i0 + 1], pos[i1 + 2] - pos[i0 + 2]]);
        var e2 = norm([pos[i2] - pos[i0], pos[i2 + 1] - pos[i0 + 1], pos[i2 + 2] - pos[i0 + 2]]);
        ang[t * 3 + v] = Math.acos(Math.max(-1, Math.min(1, dot(e1, e2))));
        var key = q(pos[i0]) + ',' + q(pos[i0 + 1]) + ',' + q(pos[i0 + 2]);
        (at[key] || (at[key] = [])).push(t * 3 + v);
      }
    }
    for (var k = 0; k < n; k++) {
      var nk = [nrm[k * 9], nrm[k * 9 + 1], nrm[k * 9 + 2]];
      for (var j = 0; j < 3; j++) {
        var b = k * 9 + j * 3, key2 = q(pos[b]) + ',' + q(pos[b + 1]) + ',' + q(pos[b + 2]), s = [0, 0, 0];
        (at[key2] || []).forEach(function (c) {
          var u = Math.floor(c / 3), nu = [nrm[u * 9], nrm[u * 9 + 1], nrm[u * 9 + 2]];
          if (u === k || dot(nu, nk) >= cosMin) { var w = ang[c]; s[0] += nu[0] * w; s[1] += nu[1] * w; s[2] += nu[2] * w; }
        });
        var m = norm(dot(s, s) > 1e-12 ? s : nk);
        out[b] = m[0]; out[b + 1] = m[1]; out[b + 2] = m[2];
      }
    }
    return out;
  };

  // a triangle record; p = the three corners as 9 floats, wound counter-clockwise seen from outside
  function tri(a, b, c, m, part, bevel) {
    return {p: [a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2]], m: m, part: part || '', bevel: bevel ? 1 : 0, atlas: null};
  }
  // turn a triangle so its normal points away from `inside` (a point inside the part)
  function outward(t, inside) {
    var p = t.p, n = triNormal(p), c = [(p[0] + p[3] + p[6]) / 3, (p[1] + p[4] + p[7]) / 3, (p[2] + p[5] + p[8]) / 3];
    if (dot(n, sub(c, inside)) < 0) {
      var x = p[3], y = p[4], z = p[5]; p[3] = p[6]; p[4] = p[7]; p[5] = p[8]; p[6] = x; p[7] = y; p[8] = z;
    }
    return t;
  }

  // ---------------------------------------------------------------- 2D polygon helpers
  function area2(pts) { var s = 0; for (var i = 0; i < pts.length; i++) { var a = pts[i], b = pts[(i + 1) % pts.length]; s += a[0] * b[1] - b[0] * a[1]; } return s / 2; }
  function ccw(pts) { return area2(pts) < 0 ? pts.slice().reverse() : pts.slice(); }
  // ear clipping on a simple polygon (counter-clockwise) -> index triples
  function earcut(pts) {
    var idx = [], out = [], n = pts.length, guard = 0, i;
    for (i = 0; i < n; i++) idx.push(i);
    function inside(p, a, b, c) {
      var d1 = (p[0] - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (p[1] - b[1]);
      var d2 = (p[0] - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (p[1] - c[1]);
      var d3 = (p[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (p[1] - a[1]);
      var neg = d1 < 0 || d2 < 0 || d3 < 0, pos = d1 > 0 || d2 > 0 || d3 > 0;
      return !(neg && pos);
    }
    while (idx.length > 3 && guard++ < 1000) {
      var cut = false;
      for (i = 0; i < idx.length; i++) {
        var i0 = idx[(i + idx.length - 1) % idx.length], i1 = idx[i], i2 = idx[(i + 1) % idx.length];
        var a = pts[i0], b = pts[i1], c = pts[i2];
        if ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) <= 1e-12) continue;   // reflex corner
        var ear = true;
        for (var k = 0; k < idx.length && ear; k++) {
          var j = idx[k];
          if (j !== i0 && j !== i1 && j !== i2 && inside(pts[j], a, b, c)) ear = false;
        }
        if (!ear) continue;
        out.push([i0, i1, i2]); idx.splice(i, 1); cut = true; break;
      }
      if (!cut) break;
    }
    if (idx.length === 3) out.push([idx[0], idx[1], idx[2]]);
    return out;
  }
  // the polygon moved inward by d along each corner's bisector (planforms are near-convex, so this is enough)
  function inset(pts, d) {
    var n = pts.length, out = [];
    for (var i = 0; i < n; i++) {
      var a = pts[(i + n - 1) % n], b = pts[i], c = pts[(i + 1) % n];
      var e1 = [b[0] - a[0], b[1] - a[1]], e2 = [c[0] - b[0], c[1] - b[1]];
      var l1 = Math.hypot(e1[0], e1[1]) || 1, l2 = Math.hypot(e2[0], e2[1]) || 1;
      var n1 = [-e1[1] / l1, e1[0] / l1], n2 = [-e2[1] / l2, e2[0] / l2];   // left normals = inward for ccw
      var bx = n1[0] + n2[0], by = n1[1] + n2[1], bl = Math.hypot(bx, by) || 1;
      var cosHalf = Math.max(0.35, (bx * n1[0] + by * n1[1]) / bl);
      out.push([b[0] + bx / bl * d / cosHalf, b[1] + by / bl * d / cosHalf]);
    }
    return out;
  }

  // ---------------------------------------------------------------- primitives (local space; nose toward -z, y up)
  // a superellipse ring of n points: half width w, half height h, centre height y; belly scales the lower half
  function ring(n, w, h, y, belly, e) {
    var out = [];
    e = e || 2.4;
    for (var i = 0; i < n; i++) {
      var a = (i / n) * Math.PI * 2, c = Math.cos(a), s = Math.sin(a);
      var x = w * (c < 0 ? -1 : 1) * Math.pow(Math.abs(c), 2 / e);
      var yy = h * (s < 0 ? -1 : 1) * Math.pow(Math.abs(s), 2 / e);
      if (yy < 0) yy *= (belly == null ? 1 : belly);
      out.push([x, yy + y]);
    }
    return out;
  }
  // loft: rings at z stations joined by quads, capped at both ends. sec = [[z, w, h, y, belly], ...]
  function loft(part, lod) {
    var n = Math.max(3, lod && part.sidesLo ? part.sidesLo : (part.sides || 8)), out = [];
    var rings = part.sec.map(function (s) { return {z: s[0], pts: ring(n, s[1], s[2], s[3] || 0, s[4], part.round)}; });
    for (var k = 0; k < rings.length - 1; k++) {
      var A = rings[k], B = rings[k + 1];
      var mid = [0, ((A.pts.reduce(function (s, p) { return s + p[1]; }, 0) + B.pts.reduce(function (s, p) { return s + p[1]; }, 0)) / (2 * n)), (A.z + B.z) / 2];
      for (var i = 0; i < n; i++) {
        var j = (i + 1) % n;
        var a0 = [A.pts[i][0], A.pts[i][1], A.z], a1 = [A.pts[j][0], A.pts[j][1], A.z];
        var b0 = [B.pts[i][0], B.pts[i][1], B.z], b1 = [B.pts[j][0], B.pts[j][1], B.z];
        var collapsedA = Math.abs(a0[0] - a1[0]) + Math.abs(a0[1] - a1[1]) < 1e-9;
        var collapsedB = Math.abs(b0[0] - b1[0]) + Math.abs(b0[1] - b1[1]) < 1e-9;
        if (!collapsedA) out.push(outward(tri(a0, a1, b1, part.mat, part.name), mid));
        if (!collapsedB) out.push(outward(tri(a0, b1, b0, part.mat, part.name), mid));
      }
    }
    [rings[0], rings[rings.length - 1]].forEach(function (R, end) {
      var big = R.pts.some(function (p, i) { return Math.hypot(p[0] - R.pts[0][0], p[1] - R.pts[0][1]) > 1e-6; });
      if (!big) return;
      var cx = 0, cy = 0; R.pts.forEach(function (p) { cx += p[0]; cy += p[1]; }); cx /= n; cy /= n;
      var inside = [cx, cy, R.z + (end ? -1 : 1) * 0.01];
      for (var i = 0; i < n; i++) {
        var j = (i + 1) % n;
        out.push(outward(tri([cx, cy, R.z], [R.pts[i][0], R.pts[i][1], R.z], [R.pts[j][0], R.pts[j][1], R.z], part.capMat || part.mat, part.name), inside));
      }
    });
    return out;
  }
  // extrude a 2D outline by `thick` along one axis, with a hard bevel strip on the top edges when bevel > 0.
  // map(u, v, w) -> [x, y, z] places outline coordinates (u, v) and the extrusion height w in local space.
  function extrude(pts, thick, bevel, part, map, stats) {
    var P = ccw(pts), out = [], h = thick / 2, b = Math.min(bevel || 0, thick * 0.45);
    var top = b > 0 ? inset(P, b) : P, n = P.length, inside = map(0, 0, 0);
    var cu = 0, cv = 0; P.forEach(function (p) { cu += p[0]; cv += p[1]; }); inside = map(cu / n, cv / n, 0);
    earcut(top).forEach(function (t) { out.push(outward(tri(map(top[t[0]][0], top[t[0]][1], h), map(top[t[1]][0], top[t[1]][1], h), map(top[t[2]][0], top[t[2]][1], h), part.mat, part.name), inside)); });
    earcut(P).forEach(function (t) { out.push(outward(tri(map(P[t[0]][0], P[t[0]][1], -h), map(P[t[1]][0], P[t[1]][1], -h), map(P[t[2]][0], P[t[2]][1], -h), part.underMat || part.mat, part.name), inside)); });
    var hs = b > 0 ? h - b : h;
    for (var i = 0; i < n; i++) {
      var j = (i + 1) % n;
      var a0 = map(P[i][0], P[i][1], -h), a1 = map(P[j][0], P[j][1], -h), b0 = map(P[i][0], P[i][1], hs), b1 = map(P[j][0], P[j][1], hs);
      var edgeIn = map((P[i][0] + P[j][0]) / 2 - (P[j][1] - P[i][1]) * 0.001, (P[i][1] + P[j][1]) / 2 + (P[j][0] - P[i][0]) * 0.001, 0);
      out.push(outward(tri(a0, a1, b1, part.edgeMat || part.mat, part.name), edgeIn));
      out.push(outward(tri(a0, b1, b0, part.edgeMat || part.mat, part.name), edgeIn));
      if (b > 0) {
        var t0 = map(top[i][0], top[i][1], h), t1 = map(top[j][0], top[j][1], h);
        out.push(outward(tri(b0, b1, t1, part.bevelMat || part.mat, part.name, 1), edgeIn));
        out.push(outward(tri(b0, t1, t0, part.bevelMat || part.mat, part.name, 1), edgeIn));
        if (stats) stats.bevels++;
      }
    }
    return out;
  }
  // slab: a planform [[x, f], ...] (f forward, toward -z) made a thin horizontal plate
  function slab(part, lod, stats) {
    return extrude(part.pts, part.thick || 0.2, lod ? 0 : part.bevel, part, function (u, v, w) { return [u, w, -v]; }, stats);
  }
  // fin: a profile [[a, b], ...] (a backward along +z, b up) made a thin upright plate
  function fin(part, lod, stats) {
    return extrude(part.pts, part.thick || 0.12, lod ? 0 : part.bevel, part, function (u, v, w) { return [w, v, u]; }, stats);
  }
  // cone or cylinder along z: r0 at z0, r1 at z1 (either radius 0 = a point); caps where the radius is not 0
  function cyl(part, lod) {
    var n = Math.max(3, lod && part.sidesLo ? part.sidesLo : (part.sides || 8));
    var r0 = part.r0, r1 = part.r1 == null ? part.r0 : part.r1;
    var sec = [[part.z0, r0, r0 * (part.squash || 1), 0, 1], [part.z1, r1, r1 * (part.squash || 1), 0, 1]];
    return loft({name: part.name, mat: part.mat, capMat: part.capMat, sides: n, sec: sec, round: 2}, false);
  }
  // box: half sizes [x, y, z], optional taper of the top face (k < 1 narrows it)
  function box(part) {
    var s = part.size, k = part.taper == null ? 1 : part.taper, out = [];
    var v = [[-s[0], -s[1], -s[2]], [s[0], -s[1], -s[2]], [s[0], s[1] , -s[2]], [-s[0], s[1], -s[2]],
             [-s[0], -s[1], s[2]], [s[0], -s[1], s[2]], [s[0], s[1], s[2]], [-s[0], s[1], s[2]]];
    [2, 3, 6, 7].forEach(function (i) { v[i][0] *= k; v[i][2] *= k; });
    [[0, 1, 2, 3], [5, 4, 7, 6], [4, 0, 3, 7], [1, 5, 6, 2], [3, 2, 6, 7], [4, 5, 1, 0]].forEach(function (f) {
      out.push(outward(tri(v[f[0]], v[f[1]], v[f[2]], part.mat, part.name), [0, 0, 0]));
      out.push(outward(tri(v[f[0]], v[f[2]], v[f[3]], part.mat, part.name), [0, 0, 0]));
    });
    return out;
  }
  // a flat quad facing +y (or along `facing`), for decals, nozzle discs and lamp plates
  function quad(part) {
    var w = part.w / 2, l = part.l / 2;
    var a = [-w, 0, -l], b = [w, 0, -l], c = [w, 0, l], d = [-w, 0, l];
    return [tri(a, d, c, part.mat, part.name), tri(a, c, b, part.mat, part.name)];
  }
  // polyhedron given as points and faces (indices; polygons are fanned), for the meshes a skin carries whole
  function mesh(part) {
    var out = [], V = part.v;
    part.f.forEach(function (f) {
      for (var i = 1; i + 1 < f.length; i++) out.push(tri(V[f[0]], V[f[i]], V[f[i + 1]], part.mat, part.name));
    });
    return out;
  }
  // torus around the z axis: major radius R, tube radius r (ry squashes the tube), M segments round, m round the tube;
  // M = 6 or 8 gives a hex or octagon ring
  function torus(part, lod) {
    var M = Math.max(3, lod && part.segLo ? part.segLo : (part.seg || 16)), m = Math.max(3, lod && part.tubeLo ? part.tubeLo : (part.tube || 6));
    var R = part.R, r = part.r, ry = part.ry || r, out = [], ph = part.phase || 0;
    function at(i, j) {
      var a = ph + i / M * Math.PI * 2, b = j / m * Math.PI * 2, rr = R + r * Math.cos(b);
      return [rr * Math.cos(a), rr * Math.sin(a), ry * Math.sin(b)];
    }
    for (var i = 0; i < M; i++) for (var j = 0; j < m; j++) {
      var a = at(i, j), b = at(i + 1, j), c = at(i + 1, j + 1), d = at(i, j + 1);
      var ai = ph + (i + 0.5) / M * Math.PI * 2, ctr = [R * Math.cos(ai), R * Math.sin(ai), 0];
      out.push(outward(tri(a, b, c, part.mat, part.name), ctr));
      out.push(outward(tri(a, c, d, part.mat, part.name), ctr));
    }
    return out;
  }
  var OPS = {loft: loft, slab: slab, fin: fin, cyl: cyl, box: box, quad: quad, mesh: mesh, torus: torus};
  HK.OPS = Object.keys(OPS);

  // ---------------------------------------------------------------- transforms
  function rotate(ts, axis, a) {
    if (!a) return;
    var c = Math.cos(a), s = Math.sin(a);
    ts.forEach(function (t) {
      var p = t.p;
      for (var i = 0; i < 9; i += 3) {
        var x = p[i], y = p[i + 1], z = p[i + 2];
        if (axis === 'x') { p[i + 1] = y * c - z * s; p[i + 2] = y * s + z * c; }
        else if (axis === 'y') { p[i] = x * c + z * s; p[i + 2] = -x * s + z * c; }
        else { p[i] = x * c - y * s; p[i + 1] = x * s + y * c; }
      }
    });
  }
  function moveBy(ts, d) { ts.forEach(function (t) { for (var i = 0; i < 9; i += 3) { t.p[i] += d[0]; t.p[i + 1] += d[1]; t.p[i + 2] += d[2]; } }); }
  function scaleBy(ts, s) { ts.forEach(function (t) { for (var i = 0; i < 9; i += 3) { t.p[i] *= s[0]; t.p[i + 1] *= s[1]; t.p[i + 2] *= s[2]; } }); }
  // mirror across x = 0: a new copy, wound the other way round so it still faces out
  function mirrorX(ts) {
    return ts.map(function (t) {
      var p = t.p;
      return {p: [-p[0], p[1], p[2], -p[6], p[7], p[8], -p[3], p[4], p[5]], m: t.m, part: t.part, bevel: t.bevel, atlas: t.atlas, letter: t.letter, side: -1,
              nobake: t.nobake};
    });
  }
  HK.mirrorX = mirrorX;

  // ---------------------------------------------------------------- recipes
  // recipe = {span|length, mats: {name: {c, grad?, emit?, glow?, gloss?}}, parts: [{name, op, mat, ..., rot?, at?, scale?, mirror?,
  // atlas?, hiOnly?, loOnly?, bake?}]}. glow 0-1: a surface partly lit from inside in 3D (a lit canopy; emit is 1, and only
  // emit marks a 2D sprite letter); bake: false keeps a part out of the 2D bake (it must lie inside the hull's extent). lod 1 (the light hull: wingmen, far craft) drops bevels and hiOnly parts and uses sidesLo.
  // The finished hull is scaled so its x extent is `span` (or its z extent is `length`) in world units.
  HK.build = function (recipe, opts) {
    opts = opts || {};
    // {"from": "own", "lod": 1, "mats": {...}}: another recipe of the same skin at a lighter level, with its own colours
    if (recipe && recipe.from) {
      var base = (opts.models || {})[recipe.from];
      if (!base || base.from) throw new Error('hull: ' + recipe.from + ' is not a recipe to build from');
      var merged = {};
      Object.keys(base).forEach(function (k) { merged[k] = base[k]; });
      Object.keys(recipe).forEach(function (k) { if (k !== 'from' && k !== 'lod') merged[k] = recipe[k]; });
      merged.mats = Object.assign({}, base.mats || {}, recipe.mats || {});
      return HK.build(merged, {lod: recipe.lod != null ? recipe.lod : opts.lod, models: opts.models});
    }
    var lod = opts.lod ? 1 : 0, tris = [], stats = {tris: 0, bevels: 0, atlasPanels: 0, parts: [], span: 0, length: 0, height: 0};
    var panels = {};
    (recipe.parts || []).forEach(function (part) {
      if (lod && part.hiOnly) return;
      if (!lod && part.loOnly) return;
      var op = OPS[part.op];
      if (!op) throw new Error('hull: unknown part op ' + part.op);
      var bev = {bevels: 0};
      var ts = op(part, lod, bev);
      if (part.scale) scaleBy(ts, part.scale.length ? part.scale : [part.scale, part.scale, part.scale]);
      if (part.rot) { rotate(ts, 'z', part.rot[2] || 0); rotate(ts, 'x', part.rot[0] || 0); rotate(ts, 'y', part.rot[1] || 0); }
      if (part.at) moveBy(ts, part.at);
      if (part.atlas) { ts.forEach(function (t) { t.atlas = part.atlas; }); panels[part.atlas] = 1; }
      if (part.letter) ts.forEach(function (t) { t.letter = 1; });
      if (part.bake === false) ts.forEach(function (t) { t.nobake = 1; });   // a 3D-only detail (light strips): the 2D bake skips it
      var copies = part.mirror ? ts.concat(mirrorX(ts)) : ts;
      stats.bevels += bev.bevels * (part.mirror ? 2 : 1);
      if (part.name && stats.parts.indexOf(part.name) < 0) stats.parts.push(part.name);
      tris = tris.concat(copies);
    });
    // drop slivers (a collapsed loft end leaves none, a degenerate planform might)
    tris = tris.filter(function (t) { return triArea(t.p) > 1e-9; });
    var lo = [1e9, 1e9, 1e9], hi = [-1e9, -1e9, -1e9];
    tris.forEach(function (t) { for (var i = 0; i < 9; i++) { var a = i % 3; if (t.p[i] < lo[a]) lo[a] = t.p[i]; if (t.p[i] > hi[a]) hi[a] = t.p[i]; } });
    var k = 1;
    if (recipe.span) k = recipe.span / Math.max(1e-6, hi[0] - lo[0]);
    else if (recipe.length) k = recipe.length / Math.max(1e-6, hi[2] - lo[2]);
    if (k !== 1) { scaleBy(tris, [k, k, k]); for (var a = 0; a < 3; a++) { lo[a] *= k; hi[a] *= k; } }
    stats.k = k;
    stats.tris = tris.length;
    stats.span = +(hi[0] - lo[0]).toFixed(3); stats.height = +(hi[1] - lo[1]).toFixed(3); stats.length = +(hi[2] - lo[2]).toFixed(3);
    stats.atlasPanels = Object.keys(panels).length;
    stats.panels = Object.keys(panels);
    stats.bbox = [lo, hi];
    stats.mats = {};
    tris.forEach(function (t) { stats.mats[t.m] = (stats.mats[t.m] || 0) + 1; });
    // the recipe's anchor lists (engines [x, y, z, r], tells, lights) in world units, scaled with the hull
    var anchors = {};
    ['engines', 'tells', 'lights'].forEach(function (key) {
      anchors[key] = (recipe[key] || []).map(function (a) { return a.map(function (v) { return v * k; }); });
    });
    anchors.strips = HK.stripPoints(tris, recipe.mats || {}, Math.max(stats.span, stats.length), anchors.engines);
    return {tris: tris, stats: stats, recipe: recipe, lod: lod, anchors: anchors};
  };
  // Recipes may be authored +Z forward. The flight and sprite frames use -Z forward.
  HK.flightBuilt = function (b) {
    if (!b || b.flightFrame || b.recipe.forward !== '+z') return b;
    b.tris.forEach(function (t) { for (var i=0;i<9;i+=3) { t.p[i]=-t.p[i]; t.p[i+2]=-t.p[i+2]; } });
    Object.keys(b.anchors).forEach(function (k) { (b.anchors[k]||[]).forEach(function (a) { a[0]=-a[0]; a[2]=-a[2]; if(k==='strips'){a[4]=-a[4];a[6]=-a[6];} }); });
    var lo=b.stats.bbox[0],hi=b.stats.bbox[1]; b.stats.bbox=[[-hi[0],lo[1],-hi[2]],[-lo[0],hi[1],-lo[2]]];
    b.flightFrame=true; return b;
  };
  // where the emissive faces glow (light strips, the canopy's lit spine): points on the emissive faces of each part, one
  // side at a time, at least a step apart (about a 16th of the craft) along the part's longest run, each
  // [x, y, z, r, nx, ny, nz, colour] with r about the strip's width and n the way its faces look (a glow sits just off
  // the hull); a part shorter than a step (a lamp) or at an engine (a nozzle) has none; at most 24 a craft. The craft
  // draw a soft halo on each (additive sprites in the one glow buffer), so an emissive strip blooms instead of
  // reading as flat paint.
  HK.stripPoints = function (tris, mats, size, engines) {
    var groups = {}, order = [], step = Math.max(0.2, (size || 14) / 16), out = [];
    tris.forEach(function (t) {
      var mat = mats[t.m] || {};
      if (!(mat.emit || +mat.glow >= 0.5)) return;
      var key = t.part + '|' + (t.side < 0 ? -1 : 1), g = groups[key];
      if (!g) { g = groups[key] = {lo: [1e9, 1e9, 1e9], hi: [-1e9, -1e9, -1e9], n: [0, 0, 0], a: 0, c: mat.c || '#ffffff', pts: []}; order.push(key); }
      for (var i = 0; i < 9; i++) { var ax = i % 3; if (t.p[i] < g.lo[ax]) g.lo[ax] = t.p[i]; if (t.p[i] > g.hi[ax]) g.hi[ax] = t.p[i]; }
      var nn = triNormal(t.p), ar = triArea(t.p);
      g.n[0] += nn[0] * ar; g.n[1] += nn[1] * ar; g.n[2] += nn[2] * ar; g.a += ar;
      // the centroid of a face, and of each half of a long sliver (a strip is a few long triangles: its points must lie on it)
      var c0 = [(t.p[0] + t.p[3] + t.p[6]) / 3, (t.p[1] + t.p[4] + t.p[7]) / 3, (t.p[2] + t.p[5] + t.p[8]) / 3];
      g.pts.push(c0);
      for (var v = 0; v < 3; v++) g.pts.push([(c0[0] + t.p[v * 3]) / 2, (c0[1] + t.p[v * 3 + 1]) / 2, (c0[2] + t.p[v * 3 + 2]) / 2]);
    });
    order.forEach(function (key) {
      var g = groups[key], d = sub(g.hi, g.lo), ax = d[0] >= d[1] && d[0] >= d[2] ? 0 : d[2] >= d[1] ? 2 : 1;
      if (Math.max(d[0], d[1], d[2]) < step) return;   // a lamp: already a point of light (the craft's lights)
      var mid = [(g.lo[0] + g.hi[0]) / 2, (g.lo[1] + g.hi[1]) / 2, (g.lo[2] + g.hi[2]) / 2];
      if ((engines || []).some(function (e) { return Math.hypot(e[0] - mid[0], e[1] - mid[1], e[2] - mid[2]) < 1.5 + 2 * (e[3] || 0.3); })) return;   // a nozzle: the engine's glow
      var r = Math.max(0.12, Math.min(0.8, g.a / Math.max(1e-6, d[ax]) / 2));   // about the strip's width (one face of it)
      var nm = norm(g.n), last = null, n = 0;
      g.pts.sort(function (p, q) { return p[ax] - q[ax]; }).forEach(function (p) {
        if (n >= 8 || (last && Math.abs(p[ax] - last[ax]) < step)) return;
        out.push([p[0], p[1], p[2], +r.toFixed(3), nm[0], nm[1], nm[2], g.c]); last = p; n++;
      });
    });
    return out.length > 24 ? out.filter(function (x, i) { return i % Math.ceil(out.length / 24) === 0; }) : out;
  };
  // colour masses of a built hull: its faces grouped by material colour into hue families (within 15 degrees; colours
  // under 0.15 saturation are one light or one dark family), weighted by area, emissive faces their own family; a family
  // holding at least `min` of the area counts (the o5 bar: one big light mass, one saturated mass, one accent)
  HK.colourMasses = function (built, min, colours) {
    min = min == null ? 0.03 : min;
    var mats = built.recipe.mats || {}, fams = [], total = 0;
    built.tris.forEach(function (t) {
      var a = triArea(t.p), mat = mats[t.m] || {c: '#808080'}, rgb = hexRGB((colours && colours[t.m]) || mat.c);
      var mx = Math.max(rgb[0], rgb[1], rgb[2]), mn = Math.min(rgb[0], rgb[1], rgb[2]), s = mx ? (mx - mn) / mx : 0, h = 0;
      if (mx !== mn) {
        if (mx === rgb[0]) h = 60 * (((rgb[1] - rgb[2]) / (mx - mn)) % 6);
        else if (mx === rgb[1]) h = 60 * ((rgb[2] - rgb[0]) / (mx - mn) + 2);
        else h = 60 * ((rgb[0] - rgb[1]) / (mx - mn) + 4);
      }
      if (h < 0) h += 360;
      var key = (mat.emit || mat.glow >= 0.5) ? 'emit' : s < 0.15 ? (mx > 0.5 ? 'light' : 'dark') : null;
      var fam = null;
      for (var i = 0; i < fams.length && !fam; i++) {
        var f = fams[i];
        if (key ? f.key === key : (!f.key && Math.min(Math.abs(f.h - h), 360 - Math.abs(f.h - h)) <= 15)) fam = f;
      }
      if (!fam) { fam = {key: key, h: h, area: 0}; fams.push(fam); }
      fam.area += a; total += a;
    });
    var out = fams.filter(function (f) { return f.area / Math.max(1e-9, total) >= min; });
    return {count: out.length, families: fams.map(function (f) { return {key: f.key || ('h' + Math.round(f.h)), share: +(f.area / total).toFixed(3)}; })};
  };

  // Skin dictionaries override supplied fields and retain neutral defaults for omitted fields.
  var OVERLAY = ['models3d', 'palettes3d', 'feel3d', 'atlas3d', 'look3d'];
  HK.resolve = function (skin, neutral) {
    var base = neutral || {}, custom = skin || {};
    var result = Object.assign({name: custom.name || base.name || 'neutral'}, base);
    Object.keys(custom).forEach(function (key) { if (OVERLAY.indexOf(key) === -1) result[key] = custom[key]; });
    result.overlay = {};
    function dictionary(value) { return !!value && typeof value === 'object' && !Array.isArray(value); }
    OVERLAY.forEach(function (key) {
      var ownValue = custom[key], defaultValue = base[key];
      if (dictionary(ownValue) && dictionary(defaultValue)) {
        var merged = Object.assign({}, defaultValue, ownValue);
        result[key] = merged;
        Object.keys(merged).forEach(function (field) {
          result.overlay[key + '.' + field] = Object.prototype.hasOwnProperty.call(ownValue, field) ? 'skin' : 'neutral';
        });
      } else {
        var origin = ownValue !== undefined ? 'skin' : 'neutral';
        var value = origin === 'skin' ? ownValue : defaultValue;
        if (value !== undefined) { result[key] = value; result.overlay[key] = origin; }
      }
    });
    return result;
  };

  // ---------------------------------------------------------------- colours
  function hexRGB(h) {
    var m = /^#?([0-9a-f]{6})$/i.exec(String(h || ''));
    var n = m ? parseInt(m[1], 16) : 0x888888;
    return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255];
  }
  HK.hexRGB = hexRGB;

  // ---------------------------------------------------------------- three.js geometry
  // per-vertex colour = the material colour x a painted gradient over the part's height and length (grad [bottom, top],
  // its spread widened o.gradK times about its middle: 1 = as written, the craft paint at 2 so a flat face reads as a
  // painted gradient and not one flat colour); bevel strips catch the light (x1.16); uv = a planar projection of each face
  // (its dominant axis) into the part's atlas panel.
  HK.toThree = function (THREE, built, o) {
    o = o || {};
    var tris = built.tris, n = tris.length, mats = Object.assign({}, built.recipe.mats || {}, o.mats || {});
    var colours = o.colours || {}, atlas = o.atlas || null, white = atlas ? atlas.white : [0.001, 0.001, 0.002, 0.002], gk = o.gradK > 0 ? o.gradK : 1;
    var pos = new Float32Array(n * 9), nrm = new Float32Array(n * 9), col = new Float32Array(n * 9), uv = new Float32Array(n * 6), emit = new Float32Array(n * 3), thrust = new Float32Array(n * 3);
    var partBox = {};
    tris.forEach(function (t) {
      var b = partBox[t.part] || (partBox[t.part] = [1e9, 1e9, 1e9, -1e9, -1e9, -1e9]);
      for (var i = 0; i < 9; i++) { var a = i % 3; if (t.p[i] < b[a]) b[a] = t.p[i]; if (t.p[i] > b[a + 3]) b[a + 3] = t.p[i]; }
    });
    var c = new THREE.Color();
    tris.forEach(function (t, k) {
      var nn = triNormal(t.p), mat = mats[t.m] || {c: '#808080'}, hex = colours[t.m] || mat.c, b = partBox[t.part];
      c.setStyle(hex);
      var lin = [c.r, c.g, c.b], grad = mat.grad || [0.82, 1.06], bev = t.bevel ? 1.16 : 1;
      var ax = Math.abs(nn[0]) > Math.abs(nn[1]) ? (Math.abs(nn[0]) > Math.abs(nn[2]) ? 0 : 2) : (Math.abs(nn[1]) > Math.abs(nn[2]) ? 1 : 2);
      var panel = t.atlas && atlas && atlas.panels[t.atlas] ? atlas.panels[t.atlas] : white;
      if (t.letter && atlas && o.letter) panel = atlas.letter(o.letter) || panel;
      for (var v = 0; v < 3; v++) {
        var x = t.p[v * 3], y = t.p[v * 3 + 1], z = t.p[v * 3 + 2];
        pos[k * 9 + v * 3] = x; pos[k * 9 + v * 3 + 1] = y; pos[k * 9 + v * 3 + 2] = z;
        nrm[k * 9 + v * 3] = nn[0]; nrm[k * 9 + v * 3 + 1] = nn[1]; nrm[k * 9 + v * 3 + 2] = nn[2];
        var ty = (y - b[1]) / Math.max(1e-6, b[4] - b[1]), tz = (z - b[2]) / Math.max(1e-6, b[5] - b[2]);
        var cx = Math.min(1, Math.abs((x - b[0]) / Math.max(1e-6, b[3] - b[0]) - 0.5) * 2);   // 0 on the part's centre line, 1 at its tips
        var mid = (grad[0] + grad[1]) / 2, g0 = mid + (grad[0] - mid) * gk, g1 = mid + (grad[1] - mid) * gk;
        var tt = gk === 1 ? 0.7 * ty + 0.3 * (1 - tz) : 0.5 * ty + 0.25 * (1 - tz) + 0.25 * (1 - cx);   // painted: lit root, darker tips
        var shade = Math.max(0.2, g0 + (g1 - g0) * tt) * bev;
        col[k * 9 + v * 3] = Math.min(1, lin[0] * shade); col[k * 9 + v * 3 + 1] = Math.min(1, lin[1] * shade); col[k * 9 + v * 3 + 2] = Math.min(1, lin[2] * shade);
        var su, sv;
        if (ax === 1) { su = (x - b[0]) / Math.max(1e-6, b[3] - b[0]); sv = (z - b[2]) / Math.max(1e-6, b[5] - b[2]); }
        else if (ax === 0) { su = (z - b[2]) / Math.max(1e-6, b[5] - b[2]); sv = (y - b[1]) / Math.max(1e-6, b[4] - b[1]); }
        else { su = (x - b[0]) / Math.max(1e-6, b[3] - b[0]); sv = (y - b[1]) / Math.max(1e-6, b[4] - b[1]); }
        if (t.side < 0 && ax !== 0) su = 1 - su;   // a mirrored copy reads its decal the right way round
        uv[k * 6 + v * 2] = panel[0] + (panel[2] - panel[0]) * su; uv[k * 6 + v * 2 + 1] = panel[1] + (panel[3] - panel[1]) * sv;
        thrust[k * 3 + v] = mat.thrust ? 1 : 0;
        emit[k * 3 + v] = mat.emit ? 1 : Math.max(0, Math.min(1, +mat.glow || 0));
      }
    });
    var geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('normal', new THREE.BufferAttribute(nrm, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
    geo.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
    geo.setAttribute('aThrust', new THREE.BufferAttribute(thrust, 1));
    geo.setAttribute('aEmit', new THREE.BufferAttribute(emit, 1));
    geo.setAttribute('aNs', new THREE.BufferAttribute(HK.creaseNormals(pos, nrm), 3));   // the rim's creased normals
    geo.computeBoundingSphere();
    return geo;
  };

  // ---------------------------------------------------------------- the decal atlas (drawn once on a canvas at load)
  // spec = {size: 512, panels: [{name, size: 64-128, bg?, draw: [{op: grid|lines|vents|stripe|text|rivets|chevron|frame, ...}]}]}
  // The atlas multiplies the hull colour: white leaves it, grey lines darken it, a coloured stripe tints it.
  HK.atlasLayout = function (spec) {
    var size = (spec && spec.size) || 512, x = 0, y = 0, row = 0, panels = {}, list = (spec && spec.panels) || [];
    // the first 16 x 16 cell is plain white: faces without a panel sample it
    var cells = [{name: '__white', size: 16}].concat(list);
    cells.forEach(function (p) {
      var s = Math.max(16, Math.min(128, p.size || 64));
      if (x + s > size) { x = 0; y += row; row = 0; }
      panels[p.name] = [x, y, s];
      x += s; row = Math.max(row, s);
    });
    // the letters strip for agent initials: 32 px cells after the panels
    if (x > 0) { x = 0; y += row; }
    var letters = {}, L = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', cell = 32, per = Math.floor(size / cell);
    for (var i = 0; i < L.length; i++) letters[L[i]] = [(i % per) * cell, y + Math.floor(i / per) * cell, cell];
    return {size: size, panels: panels, letters: letters};
  };
  HK.atlas = function (THREE, spec, doc) {
    var lay = HK.atlasLayout(spec), size = lay.size;
    doc = doc || (typeof document !== 'undefined' ? document : null);
    var canvas = doc ? doc.createElement('canvas') : null, g2 = canvas ? (canvas.width = canvas.height = size, canvas.getContext('2d')) : null;
    function uvOf(r) { var e = 0.5; return [(r[0] + e) / size, 1 - (r[1] + r[2] - e) / size, (r[0] + r[2] - e) / size, 1 - (r[1] + e) / size]; }
    var out = {texture: null, panels: {}, white: uvOf([4, 4, 8]), layout: lay, letter: function (ch) {
      var r = lay.letters[String(ch || '').toUpperCase()]; return r ? uvOf(r) : null; }};
    ((spec && spec.panels) || []).forEach(function (p) { out.panels[p.name] = uvOf(lay.panels[p.name]); out.panels[p.name].px = lay.panels[p.name][2]; });
    if (!g2) return out;
    g2.fillStyle = '#ffffff'; g2.fillRect(0, 0, size, size);
    ((spec && spec.panels) || []).forEach(function (p) {
      var r = lay.panels[p.name], X = r[0], Y = r[1], S = r[2];
      g2.save(); g2.beginPath(); g2.rect(X, Y, S, S); g2.clip();
      g2.fillStyle = p.bg || '#ffffff'; g2.fillRect(X, Y, S, S);
      (p.draw || []).forEach(function (d) { drawOp(g2, d, X, Y, S); });
      g2.restore();
    });
    // letters: a dark initial on a light badge, for the agents' liveries
    Object.keys(lay.letters).forEach(function (ch) {
      var r = lay.letters[ch], X = r[0], Y = r[1], S = r[2];
      g2.fillStyle = '#ffffff'; g2.fillRect(X, Y, S, S);
      g2.fillStyle = '#20242c'; g2.font = 'bold ' + Math.round(S * 0.78) + 'px sans-serif'; g2.textAlign = 'center'; g2.textBaseline = 'middle';
      g2.fillText(ch, X + S / 2, Y + S / 2 + S * 0.04);
    });
    var tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 4; tex.needsUpdate = true;
    tex.flipY = true;
    out.texture = tex; out.canvas = canvas;
    return out;
  };
  function drawOp(g2, d, X, Y, S) {
    var c = d.c || '#9aa3b2', w = Math.max(1, d.w || 1), i;
    var rx = function (v) { return X + v * S; }, ry = function (v) { return Y + v * S; };
    switch (d.op) {
      case 'grid':
        g2.fillStyle = c;
        for (i = 1; i < (d.n || 4); i++) { g2.fillRect(X + Math.round(i * S / (d.n || 4)), Y, w, S); g2.fillRect(X, Y + Math.round(i * S / (d.m || d.n || 4)), S, w); }
        break;
      case 'lines':
        g2.fillStyle = c;
        for (i = 0; i < (d.n || 3); i++) {
          var t = (d.at && d.at[i] != null) ? d.at[i] : (i + 1) / ((d.n || 3) + 1);
          if (d.dir === 'v') g2.fillRect(rx(t), Y, w, S); else g2.fillRect(X, ry(t), S, w);
        }
        break;
      case 'vents':
        g2.fillStyle = c;
        var vx = rx(d.x || 0.1), vy = ry(d.y || 0.6), vw = (d.wd || 0.8) * S, vh = (d.h || 0.25) * S, cols = d.cols || 6, rows = d.rows || 2;
        for (var a = 0; a < cols; a++) for (var b = 0; b < rows; b++) g2.fillRect(vx + a * vw / cols + 1, vy + b * vh / rows + 1, Math.max(1, vw / cols - 3), Math.max(1, vh / rows - 3));
        break;
      case 'stripe':
        g2.fillStyle = c;
        if (d.dir === 'v') g2.fillRect(rx(d.x || 0.4), Y, (d.wd || 0.12) * S, S); else g2.fillRect(X, ry(d.y || 0.4), S, (d.h || 0.12) * S);
        break;
      case 'chevron':
        g2.fillStyle = c; g2.beginPath();
        var cy = ry(d.y || 0.5), hh = (d.h || 0.18) * S;
        g2.moveTo(X, cy - hh); g2.lineTo(X + S / 2, cy); g2.lineTo(X + S, cy - hh); g2.lineTo(X + S, cy); g2.lineTo(X + S / 2, cy + hh); g2.lineTo(X, cy); g2.closePath(); g2.fill();
        break;
      case 'rivets':
        g2.fillStyle = c;
        for (i = 0; i < (d.n || 8); i++) { g2.fillRect(X + 3 + (i % 8) * (S - 6) / 7, ry(d.y || 0.08) + Math.floor(i / 8) * 6, 2, 2); }
        break;
      case 'frame':
        g2.fillStyle = c; g2.fillRect(X, Y, S, w); g2.fillRect(X, Y + S - w, S, w); g2.fillRect(X, Y, w, S); g2.fillRect(X + S - w, Y, w, S);
        break;
      case 'text':
        g2.fillStyle = c; g2.font = 'bold ' + Math.round((d.size || 0.3) * S) + 'px sans-serif'; g2.textAlign = 'center'; g2.textBaseline = 'middle';
        g2.fillText(String(d.s || ''), rx(d.x == null ? 0.5 : d.x), ry(d.y == null ? 0.5 : d.y));
        break;
      case 'checker':
        g2.fillStyle = c; var q = Math.max(2, Math.round(S / (d.n || 8)));
        for (var u = 0; u < S; u += q) for (var vv = 0; vv < S; vv += q) if (((u + vv) / q) % 2 === 0) g2.fillRect(X + u, Y + vv, q, q);
        break;
      default: break;
    }
  }

  // ---------------------------------------------------------------- the hull material
  // One warm key from the sun (uSunPos is the star's world position), near-zero ambient, three toon bands over a dark
  // floor (uFill: the shadow side keeps 7% of the key; the terminator sits where a face turns from the key, so a lit side
  // and a dark side read on every hull), the decal atlas multiplied in, a backlit fresnel rim (power uRimPow, tinted by
  // the region sky, uRimK its gain) worked out on the creased normals (aNs) so it runs as a band along the silhouette,
  // strongest on the shadow side and where the key comes from behind the craft, a facet glint toward the sun (o5 bar:
  // roughness ~0.35) and unlit emissive faces (engines, lamps, tells) lifted toward white at their core. uFlash whitens a
  // craft for a hit; uGlow scales the emissives. A geometry without aNs (all zero) takes its flat normal for the rim.
  var VERT = [
    'attribute float aEmit; attribute vec3 aNs; attribute float aThrust;',
    'varying vec3 vCol; varying vec3 vNw; varying vec3 vNs; varying vec3 vWp; varying vec2 vUv; varying float vEmit; varying float vThrust;',
    'void main(){',
    '  vCol = color; vUv = uv; vEmit = aEmit; vThrust = aThrust;',
    '  mat4 m = modelMatrix;',
    '  #ifdef USE_INSTANCING',
    '  m = modelMatrix * instanceMatrix;',
    '  #endif',
    '  vec4 wp = m * vec4(position, 1.0);',
    '  vNw = normalize(mat3(m) * normal);',
    '  vNs = mat3(m) * (dot(aNs, aNs) > 0.25 ? aNs : normal);',
    '  vWp = wp.xyz;',
    '  gl_Position = projectionMatrix * viewMatrix * wp;',
    '}'].join('\n');
  var FRAG = [
    'uniform vec3 uSunPos; uniform vec3 uSunCol; uniform vec3 uAmb; uniform vec3 uRim; uniform float uRimPow; uniform float uRimK; uniform float uFill; uniform float uHot;',
    'uniform sampler2D uAtlas; uniform float uFlash; uniform float uGlow; uniform float uThrust; uniform vec3 uFlashCol; uniform float uSpec; uniform float uShine;',
    'varying vec3 vCol; varying vec3 vNw; varying vec3 vNs; varying vec3 vWp; varying vec2 vUv; varying float vEmit; varying float vThrust;',
    'void main(){',
    '  vec3 N = normalize(vNw);',
    '  vec3 Ns = normalize(vNs);',
    '  if (!gl_FrontFacing) { N = -N; Ns = -Ns; }',
    '  vec3 L = normalize(uSunPos - vWp);',
    '  vec3 V = normalize(cameraPosition - vWp);',
    '  float w = clamp(dot(N, L) * 0.5 + 0.5, 0.0, 1.0);',
    '  float band = uFill + (0.50 - uFill) * smoothstep(0.46, 0.52, w) + 0.50 * smoothstep(0.66, 0.72, w);',
    '  vec3 dec = texture2D(uAtlas, vUv).rgb;',
    '  vec3 base = mix(vCol * dec, vec3(1.0,0.32,0.03), vThrust * uThrust);',
    '  vec3 col = base * (uAmb + uSunCol * band);',
    '  float fr = pow(1.0 - clamp(dot(Ns, V), 0.0, 1.0), uRimPow);',
    '  float back = clamp(-dot(V, L), 0.0, 1.0);   // the key behind the craft: a backlight',
    '  float edge = smoothstep(0.06, 0.42, fr);    // a band along the silhouette, not a wash over the hull',
    '  float shade = 1.0 - smoothstep(0.30, 0.62, dot(Ns, L) * 0.5 + 0.5);   // the rim lives on the shadow side',
    '  col += uRim * edge * uRimK * (0.10 + 1.0 * shade + 0.6 * back * (0.4 + 0.6 * shade));',
    '  vec3 Hh = normalize(L + V);',
    '  col += uSunCol * uSpec * pow(max(dot(N, Hh), 0.0), uShine) * step(0.0, dot(N, L));   // a glint on the facet facing the sun',
    '  vec3 hot = base * uGlow + (vec3(1.0) - base) * uHot;   // an emissive face is hot at its core',
    '  col = mix(col, hot, vEmit);',
    '  col = mix(col, uFlashCol, uFlash);',
    '  gl_FragColor = vec4(col, 1.0);',
    '  #include <tonemapping_fragment>',
    '  #include <colorspace_fragment>',
    '}'].join('\n');
  HK.material = function (THREE, atlasTex, look) {
    look = look || {};
    var m = new THREE.ShaderMaterial({
      uniforms: {
        uSunPos: {value: new THREE.Vector3(0, 4000, 0)}, uSunCol: {value: new THREE.Color(look.sun || '#fff1dc')},
        uAmb: {value: new THREE.Color(look.ambient || '#0a0c14')}, uRim: {value: new THREE.Color(look.rim || '#8fb4ff')},
        uRimPow: {value: look.rimPow || 3}, uRimK: {value: look.rimK == null ? 2.2 : look.rimK}, uFill: {value: look.fill == null ? 0.07 : look.fill},
        uHot: {value: look.hot == null ? 0.22 : look.hot},
        uThrust: {value: 0}, uAtlas: {value: atlasTex}, uFlash: {value: 0}, uGlow: {value: look.glowK == null ? 1.3 : look.glowK}, uFlashCol: {value: new THREE.Color('#ffffff')},
        uSpec: {value: look.spec == null ? 0.32 : look.spec}, uShine: {value: look.shine || 28}
      },
      vertexShader: VERT, fragmentShader: FRAG, vertexColors: true
    });
    m.userData.rim = true;
    return m;
  };
  HK.SHADERS = {vert: VERT, frag: FRAG};

  CM3D.hull = HK;
  // the module: builds the shared atlas texture once per entry and hands the kit to the craft through ctx.assets
  var kit = null;
  // the skin's 3D data with the neutral file under it (the bundle carries the neutral file as CM3D.neutral3d when the skin
  // is another one; a key the skin lacks falls through), and the decal atlas, for every module through ctx.assets
  CM3D.module('hullkit', {
    build: function (scene, world, ctx) {
      if (!ctx || !ctx.THREE) return;
      var sk = HK.resolve(ctx.skin3d || {}, CM3D.neutral3d || {});
      kit = HK.atlas(ctx.THREE, sk.atlas3d || {});
      ctx.assets = ctx.assets || {};
      ctx.assets.kit = kit;
      ctx.assets.skin3d = sk;
    },
    rebuild: function () {},
    update: function () {},
    dispose: function () { if (kit && kit.texture) kit.texture.dispose(); kit = null; }
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
