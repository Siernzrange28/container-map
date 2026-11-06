// world3d/fx.js — effects, only when the data changed: a fact moving is the only reason anything flashes.
// Registered on the 3D view's registry (world3d/core.js); reads only `world` (cmWorld()) and `ctx`, never page globals.
// The module keeps a snapshot of the last world's facts; build() takes the baseline in silence, and every later rev
// (rebuild) is diffed against it. One change makes at most one effect, at the body whose fact moved:
//   warp    a body appeared (expanding ring + sparkles in its purpose colour) or vanished (the ring contracts)
//   shock   a planet worsened, or a problem / something waiting arrived there (fast ring + outward sparks)
//   cleared a planet improved, or a problem / waiting entry left (slow ring + rising sparkles)
//   shield  work started there: a planet gained a worker, a craft began working, the star's load rose (shell pulse)
// Effects run on the world clock (update's dt is sceneDt: they freeze while paused) and are still under reduced motion
// (no travel: a held ring that only fades). Nothing loops, nothing is ambient: with no rev change the view is quiet.
// Pure helpers live on CM3D.fx for the tests; ?demo3d=fx shows the four kinds in turn over one stand-in planet.
(function (g) {
  'use strict';
  var CM3D = g.CM3D;
  if (!CM3D || typeof CM3D.module !== 'function') return;

  var LIFE = { warp: 1.1, shock: 0.9, cleared: 1.4, shield: 1.6 };       // seconds on the world clock
  var PEAK = { warp: 0.85, shock: 0.95, cleared: 0.8, shield: 0.65 };     // ring/shell alpha at the top of the attack
  var NEUTRAL_FX = { warp: '#7FD8FF', bad: '#FF5A5F', warn: '#FFB547', good: '#4ADE80', work: '#6FB7FF' };
  var MAX_LIVE = 24, MAX_PER_DIFF = 12;

  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function num(v, d) { var x = Number(v); return isFinite(x) ? x : d; }
  function easeOutCubic(u) { u = clamp(u, 0, 1); return 1 - Math.pow(1 - u, 3); }
  // health as a ladder: only a rung change (or a count moving) is news; quiet and ok share a rung
  function rank(health) { return health === 'broken' ? 2 : health === 'waiting' ? 1 : 0; }
  // an effect's ring radius as a multiple of its body's radius (it starts clear of the body, so depth keeps it honest)
  function ringScale(kind, u, shrink, reduced) {
    if (reduced) return shrink ? 1.5 : 1.6;
    var e = easeOutCubic(u);
    if (kind === 'shock') return 1.05 + 1.55 * e;
    if (kind === 'cleared') return 1.05 + 1.25 * e;
    return shrink ? 2.2 - 1.15 * e : 1.05 + 1.15 * e;                     // warp in, or back out
  }
  function shieldScale(u, reduced) { return reduced ? 1.18 : 1.02 + 0.38 * easeOutCubic(u); }
  function alphaAt(kind, u) {
    if (!(u >= 0) || u >= 1) return 0;
    var attack = u < 0.08 ? u / 0.08 : 1;
    return (PEAK[kind] || 0.8) * attack * Math.pow(1 - u, 1.6);
  }
  function seeded(seed) {
    var s = (Number(seed) || 1) >>> 0;
    return function () { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
  }

  // ------------------------------------------------------------------ the facts an effect may answer to
  // One snapshot per rev: positions ride along only so a vanished body can still be marked where it was.
  function snapshot(world) {
    var out = { planets: {}, foes: {}, waiting: {}, craft: {}, machines: {}, star: null };
    if (!world || typeof world !== 'object') return out;
    (Array.isArray(world.planets) ? world.planets : []).forEach(function (p) {
      if (!p || p.uid == null) return;
      var c = p.counts || {}, cond = Array.isArray(p.conditions) ? p.conditions : [];
      out.planets[String(p.uid)] = {
        x: num(p.x, 0), y: num(p.y, 0), r: Math.max(1, num(p.r, 30)), purpose: String(p.purpose || ''),
        rank: rank(p.health), problems: num(c.problems, 0), waitingN: num(c.need_you, 0) + num(c.decisions, 0),
        working: cond.indexOf('working') >= 0
      };
    });
    (Array.isArray(world.foes) ? world.foes : []).forEach(function (f) {
      if (f && f.uid != null) out.foes[String(f.uid)] = String(f.parent || '');
    });
    (Array.isArray(world.waiting) ? world.waiting : []).forEach(function (f) {
      if (f && f.uid != null) out.waiting[String(f.uid)] = String(f.parent || '');
    });
    (Array.isArray(world.craft) ? world.craft : []).forEach(function (c) {
      if (!c || c.uid == null) return;
      out.craft[String(c.uid)] = { state: String(c.state || ''), planet: c.planet != null ? String(c.planet) : null, x: num(c.x, 0), y: num(c.y, 0) };
    });
    (Array.isArray(world.machines) ? world.machines : []).forEach(function (m) {
      if (m && m.uid != null) out.machines[String(m.uid)] = { x: num(m.x, 0), y: num(m.y, 0) };
    });
    if (world.star) out.star = { x: num(world.star.x, 0), y: num(world.star.y, 0), r: Math.max(1, num(world.star.r, 150)), load: num(world.star.load, 0) };
    return out;
  }

  // What changed between two snapshots, as effect events. At most one event per planet per rev (shock beats cleared
  // beats shield; a problem arriving at a planet that already flashed adds nothing), so a busy rev reads as a few
  // clear marks, never a firework show. Returns {events, cut}: past MAX_PER_DIFF the lowest-priority are counted, cut.
  function diffSnaps(a, b, palette) {
    var events = [], seen = {};
    function place(snap, uid, k) { var p = snap.planets[uid]; return p ? { x: p.x, y: p.y, r: p.r * (k || 1) } : null; }
    function emit(ev) { events.push(ev); if (ev.planetUid) seen[ev.planetUid] = true; }
    Object.keys(b.planets).forEach(function (uid) {
      var n = b.planets[uid], o = a.planets[uid];
      if (!o) {
        var rgb = palette && Array.isArray(palette[n.purpose]) ? palette[n.purpose] : null;
        emit({ kind: 'warp', uid: uid, tone: 'new', planetUid: uid, at: { x: n.x, y: n.y, r: n.r }, shrink: false, rgb: rgb });
        return;
      }
      var worse = n.rank > o.rank || n.problems > o.problems || n.waitingN > o.waitingN;
      var better = n.rank < o.rank || n.problems < o.problems || n.waitingN < o.waitingN;
      if (worse) emit({ kind: 'shock', uid: uid, tone: n.rank === 2 || n.problems > o.problems ? 'bad' : 'warn', planetUid: uid, at: { x: n.x, y: n.y, r: n.r }, shrink: false, rgb: null });
      else if (better) emit({ kind: 'cleared', uid: uid, tone: 'good', planetUid: uid, at: { x: n.x, y: n.y, r: n.r }, shrink: false, rgb: null });
      else if (n.working && !o.working) emit({ kind: 'shield', uid: uid, tone: 'work', planetUid: uid, at: { x: n.x, y: n.y, r: n.r }, shrink: false, rgb: null });
    });
    Object.keys(a.planets).forEach(function (uid) {
      if (b.planets[uid]) return;
      var o = a.planets[uid];
      emit({ kind: 'warp', uid: uid, tone: 'new', planetUid: null, at: { x: o.x, y: o.y, r: o.r }, shrink: true, rgb: null });
    });
    function arrivals(mapA, mapB, kind, tone) {
      Object.keys(mapB).forEach(function (uid) {
        if (mapA[uid] !== undefined) return;
        var parent = mapB[uid]; if (!parent || seen[parent]) return;
        var at = place(b, parent, 1.1) || place(a, parent, 1.1); if (!at) return;
        emit({ kind: kind, uid: uid, tone: tone, planetUid: parent, at: at, shrink: false, rgb: null });
      });
      Object.keys(mapA).forEach(function (uid) {
        if (mapB[uid] !== undefined) return;
        var parent = mapA[uid]; if (!parent || seen[parent]) return;
        var at = place(b, parent, 1.1) || place(a, parent, 1.1); if (!at) return;
        emit({ kind: 'cleared', uid: uid, tone: 'good', planetUid: parent, at: at, shrink: false, rgb: null });
      });
    }
    arrivals(a.foes, b.foes, 'shock', 'bad');
    arrivals(a.waiting, b.waiting, 'shock', 'warn');
    Object.keys(b.craft).forEach(function (uid) {
      var n = b.craft[uid], o = a.craft[uid];
      if (!o) emit({ kind: 'warp', uid: uid, tone: 'new', planetUid: null, at: { x: n.x, y: n.y, r: 26 }, shrink: false, rgb: null });
      else if (n.state === 'working' && o.state !== 'working' && !(n.planet && seen[n.planet]))
        emit({ kind: 'shield', uid: uid, tone: 'work', planetUid: null, at: { x: n.x, y: n.y, r: 26 }, shrink: false, rgb: null });
    });
    Object.keys(a.craft).forEach(function (uid) {
      if (b.craft[uid]) return;
      var o = a.craft[uid];
      emit({ kind: 'warp', uid: uid, tone: 'new', planetUid: null, at: { x: o.x, y: o.y, r: 26 }, shrink: true, rgb: null });
    });
    Object.keys(b.machines).forEach(function (uid) {
      if (a.machines[uid]) return;
      var n = b.machines[uid];
      emit({ kind: 'warp', uid: uid, tone: 'new', planetUid: null, at: { x: n.x, y: n.y, r: 40 }, shrink: false, rgb: null });
    });
    Object.keys(a.machines).forEach(function (uid) {
      if (b.machines[uid]) return;
      var o = a.machines[uid];
      emit({ kind: 'warp', uid: uid, tone: 'new', planetUid: null, at: { x: o.x, y: o.y, r: 40 }, shrink: true, rgb: null });
    });
    if (a.star && b.star && b.star.load > a.star.load)
      emit({ kind: 'shield', uid: 'star', tone: 'work', planetUid: null, at: { x: b.star.x, y: b.star.y, r: b.star.r }, shrink: false, rgb: null });
    var prio = { shock: 0, cleared: 1, warp: 2, shield: 3 };
    events.sort(function (x, y) { return (prio[x.kind] - prio[y.kind]) || (x.uid < y.uid ? -1 : x.uid > y.uid ? 1 : 0); });
    return { events: events.slice(0, MAX_PER_DIFF), cut: Math.max(0, events.length - MAX_PER_DIFF) };
  }
  function diffWorlds(prev, next) {
    if (!prev || !next) return { events: [], cut: 0 };
    return diffSnaps(snapshot(prev), snapshot(next), next.palette || null);
  }

  // ------------------------------------------------------------------ the drawing: rings, sparkles, one shell
  var S = null;                 // this entry's state (null while the view is off or in the demo)
  function hashStr(s) { var h = 2166136261; s = String(s); for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
  function fxColors(ctx) {
    var out = {}, over = (ctx && ctx.skin3d && ctx.skin3d.fx3d) || {};
    Object.keys(NEUTRAL_FX).forEach(function (k) {
      out[k] = typeof over[k] === 'string' && /^#[0-9a-f]{6}$/i.test(over[k]) ? over[k] : NEUTRAL_FX[k];
    });
    return out;
  }
  function toneHex(ev, colors) {
    if (ev.rgb && ev.rgb.length === 3) return '#' + ev.rgb.map(function (v) { return clamp(Math.round(num(v, 0)), 0, 255).toString(16).padStart(2, '0'); }).join('');
    return colors[ev.tone === 'new' ? 'warp' : ev.tone] || colors.warp;
  }
  function dotTexture(THREE) {
    var c = document.createElement('canvas'); c.width = c.height = 64;
    var x = c.getContext('2d'), gr = x.createRadialGradient(32, 32, 0, 32, 32, 32);
    gr.addColorStop(0, 'rgba(255,255,255,1)'); gr.addColorStop(0.35, 'rgba(255,255,255,0.55)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
    x.fillStyle = gr; x.fillRect(0, 0, 64, 64);
    return new THREE.CanvasTexture(c);
  }
  function makeAssets(ctx) {
    var THREE = ctx.THREE;
    var ring = new THREE.RingGeometry(0.88, 1, 64); ring.rotateX(-Math.PI / 2);   // flat in the map plane, like an equator
    return { ring: ring, shell: new THREE.SphereGeometry(1, 28, 18), dot: dotTexture(THREE) };
  }
  function placeOf(ev) {
    var sp = ev.planetUid && CM3D.space && typeof CM3D.space.planet === 'function' ? CM3D.space.planet(ev.planetUid) : null;
    if (sp && isFinite(sp.x) && isFinite(sp.r)) return { x: sp.x, y: num(sp.y, 0), z: sp.z, r: Math.max(1, sp.r) };
    return { x: ev.at.x, y: 0, z: ev.at.y, r: Math.max(1, ev.at.r) };
  }
  function spawn(state, ctx, ev, reduced) {
    if (state.live.length >= MAX_LIVE) {
      var old = -1;
      for (var i = 0; i < state.live.length; i++) if (state.live[i].kind !== 'shock') { old = i; break; }
      if (old < 0) { state.dropped++; return null; }
      kill(state, state.live[old]); state.live.splice(old, 1); state.dropped++;
    }
    var THREE = ctx.THREE, at = placeOf(ev), hex = toneHex(ev, state.colors);
    var e = { kind: ev.kind, uid: ev.uid, tone: ev.tone, shrink: !!ev.shrink, reduced: !!reduced, t: 0, life: LIFE[ev.kind] || 1,
      x: at.x, y: at.y, z: at.z, r: at.r, rings: [], shell: null, mats: [], pts: null, pos: null, vel: null };
    function mat(opts) { var m = new THREE.MeshBasicMaterial(opts); state.mats.push(m); e.mats.push(m); return m; }
    if (ev.kind === 'shield') {
      var shell = new THREE.Mesh(state.assets.shell, mat({ color: hex, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.BackSide }));
      shell.position.set(at.x, at.y, at.z); shell.renderOrder = 30; state.root.add(shell); e.shell = shell;
    } else {
      var delays = ev.kind === 'warp' ? [0, 0.14] : [0];
      delays.forEach(function (d) {
        var ring = new THREE.Mesh(state.assets.ring, mat({ color: hex, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
        ring.position.set(at.x, at.y, at.z); ring.renderOrder = 30; ring.visible = false; state.root.add(ring);
        e.rings.push({ mesh: ring, delay: d });
      });
      var n = ctx.quality && ctx.quality.phone ? 14 : 26, rnd = seeded(hashStr(ev.uid + ':' + ev.kind) + (++state.seed) * 7919);
      e.pos = new Float32Array(n * 3); e.vel = new Float32Array(n * 3);
      for (var k = 0; k < n; k++) {
        var th = rnd() * Math.PI * 2, zz = rnd() * 2 - 1, rr = Math.sqrt(Math.max(0, 1 - zz * zz));
        var dx = Math.cos(th) * rr, dy = zz, dz = Math.sin(th) * rr, j = k * 3;
        if (ev.kind === 'warp' && !ev.shrink) {          // warp-in: the sparkles fall inward onto the new body
          e.pos[j] = at.x + dx * at.r * 2.0; e.pos[j + 1] = at.y + dy * at.r * 2.0; e.pos[j + 2] = at.z + dz * at.r * 2.0;
          e.vel[j] = -dx * at.r * 1.15; e.vel[j + 1] = -dy * at.r * 1.15; e.vel[j + 2] = -dz * at.r * 1.15;
        } else {
          var sp2 = ev.kind === 'shock' ? at.r * (1.5 + rnd() * 1.5) : at.r * (0.6 + rnd() * 0.8);
          e.pos[j] = at.x + dx * at.r * 1.03; e.pos[j + 1] = at.y + dy * at.r * 1.03; e.pos[j + 2] = at.z + dz * at.r * 1.03;
          e.vel[j] = dx * sp2; e.vel[j + 1] = dy * sp2 + (ev.kind === 'cleared' ? at.r * (0.7 + rnd() * 0.7) : 0); e.vel[j + 2] = dz * sp2;
        }
      }
      var geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(e.pos, 3));
      var pm = new THREE.PointsMaterial({ color: hex, size: Math.max(2.5, at.r * 0.14), map: state.assets.dot, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending, sizeAttenuation: true });
      state.mats.push(pm); e.mats.push(pm);
      e.pts = new THREE.Points(geo, pm); e.pts.frustumCulled = false; e.pts.renderOrder = 31; state.root.add(e.pts);
    }
    state.live.push(e); state.fired++; state.byKind[ev.kind] = (state.byKind[ev.kind] || 0) + 1;
    state.last = { kind: ev.kind, uid: ev.uid, tone: ev.tone };
    return e;
  }
  function kill(state, e) {
    e.rings.forEach(function (r) { if (r.mesh.parent) r.mesh.parent.remove(r.mesh); });
    if (e.shell && e.shell.parent) e.shell.parent.remove(e.shell);
    if (e.pts) { if (e.pts.parent) e.pts.parent.remove(e.pts); e.pts.geometry.dispose(); }
    e.mats.forEach(function (m) { m.dispose(); var i = state.mats.indexOf(m); if (i >= 0) state.mats.splice(i, 1); });
  }
  function stepAll(state, dt) {
    for (var i = state.live.length - 1; i >= 0; i--) {
      var e = state.live[i]; e.t += dt;
      var u = e.life > 0 ? e.t / e.life : 1;
      if (u >= 1) { kill(state, e); state.live.splice(i, 1); continue; }
      e.rings.forEach(function (r) {
        var uu = (u - r.delay) / Math.max(0.001, 1 - r.delay);
        r.mesh.visible = uu >= 0;
        if (uu < 0) return;
        r.mesh.scale.setScalar(e.r * ringScale(e.kind, uu, e.shrink, e.reduced));
        r.mesh.material.opacity = alphaAt(e.kind, uu);
      });
      if (e.shell) { e.shell.scale.setScalar(e.r * shieldScale(u, e.reduced)); e.shell.material.opacity = alphaAt('shield', u) * 0.95; }
      if (e.pts) {
        e.pts.material.opacity = alphaAt(e.kind, u);
        if (!e.reduced && dt > 0) {
          var drag = Math.exp(-1.6 * dt), grav = e.kind === 'shock' ? e.r * 0.35 * dt : 0;
          for (var j = 0; j < e.pos.length; j += 3) {
            e.vel[j] *= drag; e.vel[j + 1] = e.vel[j + 1] * drag - grav; e.vel[j + 2] *= drag;
            e.pos[j] += e.vel[j] * dt; e.pos[j + 1] += e.vel[j + 1] * dt; e.pos[j + 2] += e.vel[j + 2] * dt;
          }
          e.pts.geometry.attributes.position.needsUpdate = true;
        }
      }
    }
  }
  function newState(scene, ctx) {
    var st = { scene: scene, root: new ctx.THREE.Group(), assets: makeAssets(ctx), colors: fxColors(ctx),
      live: [], mats: [], snap: null, fired: 0, dropped: 0, byKind: {}, last: null, rev: null, seed: 0 };
    st.root.name = 'cm3d-fx'; scene.add(st.root);
    return st;
  }
  function disposeState(st) {
    if (!st) return;
    st.live.slice().forEach(function (e) { kill(st, e); });
    st.live = [];
    if (st.root.parent) st.root.parent.remove(st.root);
    st.mats.forEach(function (m) { m.dispose(); });
    if (st.assets) { st.assets.ring.dispose(); st.assets.shell.dispose(); st.assets.dot.dispose(); }
  }
  function writeAudit(ctx) {
    if (!S) return;
    ctx.audit.fx = { live: S.live.length, fired: S.fired, dropped: S.dropped, rev: S.rev,
      byKind: { warp: S.byKind.warp || 0, shock: S.byKind.shock || 0, cleared: S.byKind.cleared || 0, shield: S.byKind.shield || 0 },
      last: S.last ? { kind: S.last.kind, uid: S.last.uid, tone: S.last.tone } : null };
  }

  // ------------------------------------------------------------------ the module: baseline, then one mark per change
  function build(scene, world, ctx) {
    if (S) disposeState(S);
    S = newState(scene, ctx);
    S.snap = snapshot(world); S.rev = world ? world.rev : null;   // entering 3D is not news: nothing fires for what is
    writeAudit(ctx);
  }
  function rebuild(scene, world, ctx) {
    if (!S || S.scene !== scene) { build(scene, world, ctx); return; }
    var next = snapshot(world), d = diffSnaps(S.snap, next, world ? world.palette : null);
    var reduced = !!(world && world.reduced) || !!ctx.reduced;
    S.dropped += d.cut;
    d.events.forEach(function (ev) { spawn(S, ctx, ev, reduced); });
    S.snap = next; S.rev = world ? world.rev : null;
    writeAudit(ctx);
  }
  function update(world, dt, ctx) {
    if (!S) return;
    stepAll(S, Math.max(0, num(dt, 0)));
    writeAudit(ctx);
  }
  function dispose() { disposeState(S); S = null; }
  CM3D.module('fx', { build: build, rebuild: rebuild, update: update, dispose: dispose });

  // ------------------------------------------------------------------ demo page: ?demo3d=fx
  // One stand-in planet on black; the four kinds fire in turn (warp, shock, cleared, shield) so each can be looked at.
  var D = null, DEMO_SEQ = [
    { kind: 'warp', tone: 'new' }, { kind: 'shock', tone: 'bad' }, { kind: 'cleared', tone: 'good' }, { kind: 'shield', tone: 'work' }
  ];
  function demoFire(ctx) {
    var s = DEMO_SEQ[D.i % DEMO_SEQ.length]; D.i++;
    spawn(D, ctx, { kind: s.kind, uid: 'demo:planet', tone: s.tone, planetUid: null, at: { x: 0, y: 0, r: 30 }, shrink: false, rgb: null }, !!ctx.reduced);
    D.kind = s.kind; D.next = D.clock + 1.5;
  }
  if (typeof CM3D.demo === 'function') CM3D.demo('fx', {
    build: function (scene, ctx, pr) {
      var THREE = ctx.THREE;
      scene.background = new THREE.Color(0x000000);
      D = newState(scene, ctx); D.i = 0; D.clock = 0; D.next = 0; D.kind = '';
      var planet = new THREE.Mesh(new THREE.SphereGeometry(30, 48, 32), new THREE.MeshToonMaterial({ color: 0x4e79c8 }));
      planet.userData.uid = 'demo:planet'; D.root.add(planet); D.planet = planet;
      var key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(-120, 90, 140); D.root.add(key); D.root.add(key.target);
      demoFire(ctx);
      var want = Number(pr && pr.px) || 90, h = Math.max(1, g.innerHeight || 1);
      var d = 30 / (want / h * Math.tan(ctx.camera.fov * Math.PI / 360));
      ctx.camera.position.setLength(Math.max(60, d)); ctx.camera.lookAt(0, 0, 0);
      ctx.audit.ext.demo = 'fx'; ctx.audit.ext.fx = { live: D.live.length, kind: D.kind };
    },
    update: function (dt, ctx) {
      if (!D) return;
      D.clock += Math.max(0, num(dt, 0));
      if (D.clock >= D.next) demoFire(ctx);
      stepAll(D, Math.max(0, num(dt, 0)));
      var p = ctx.project(0, 0, 0);
      CM3D.fx.demo = { kind: D.kind, live: D.live.length, x: Math.round(p.x), y: Math.round(p.y) };
      ctx.audit.ext.fx = { live: D.live.length, kind: D.kind };
    },
    dispose: function () {
      if (!D) return;
      if (D.planet) { D.planet.geometry.dispose(); D.planet.material.dispose(); }
      disposeState(D); D = null; CM3D.fx.demo = null;
    }
  });

  // ------------------------------------------------------------------ the public face (pure helpers for the tests)
  CM3D.fx = {
    snapshot: snapshot, diffSnaps: diffSnaps, diffWorlds: diffWorlds, rank: rank,
    easeOutCubic: easeOutCubic, ringScale: ringScale, shieldScale: shieldScale, alphaAt: alphaAt,
    colors: function (skin3d) { return fxColors({ skin3d: skin3d || {} }); },
    LIFE: LIFE, NEUTRAL: NEUTRAL_FX,
    live: function () { return S ? S.live.length : D ? D.live.length : 0; },
    demo: null
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
