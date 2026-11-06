// world3d/camera.js — the rigs (cockpit, chase, no ship, event), the cockpit pass and its art (graphite frame, consoles,
// the MFD, two light wedges, the canopy glass, the tint and the light spill), the entry and exit timeline (match, dive,
// a once-per-session establishing orbit, the cockpit rising; the log-space rise on exit), warp streaks while shaders
// compile, "Fly to", pause with the framed 2D map, the touch deck, comfort and reduced motion.
// Registered on the 3D view's registry (world3d/core.js); reads only `world` (cmWorld()) and `ctx`, never page globals.
// The page advances the shared engine and spatial flight together. This module supplies pitch input and reads the
// resulting XYZ position and attitude; its fallback keeps older page adapters working.
//
// Shared with world3d/hud.js through CM3D.fh (one object, camera first in the bundle): the resolved numbers (fh.feel), the
// followed workflow (fh.follow), the workflow events (fh.events, only on a rev change:.9 honesty), the timeline's
// HUD switches (fh.hudAlpha, fh.caption, fh.targetCard), the settings (fh.set), the lock (written by hud.js), the input.
//
// Numbers:. Neutral values are ours (the 2D FLY values where one exists); a skin's feel3d (its 3D JSON, the
// private one only in its own bundle) replaces any of them.
(function (g) {
  'use strict';
  var C = g.CM3D;
  if (!C || typeof C.module !== 'function') return;
  var FH = C.fh = C.fh || {};
  var D2R = Math.PI / 180, R2D = 180 / Math.PI, TAU = Math.PI * 2;

  // ---------------------------------------------------------------- numbers
  var NEUTRAL = {
    fov: 55, fovBoost: 8, turn: 55, turnHard: 110, pitch: 30, bankSoft: 35, bankHard: 80, rollT: 400, rollDeg: 360, uturnT: 1200,
    noseLead: 25, speed: { cruise: 200, boost: 340, brake: 110 },
    chase: { cruise: 50, boost: 90, brake: 35, lag: 8, rollCap: 1 / 3, rollMax: 20, rollRate: 0, swing: 12, eye: 9, eyeK: 1, look: 40 },
    cockpit: { look: 0.8, glass: 0.2 }, reticle: [600, 1200], lock: { dwell: 0.8, cone: 10, hold: 0 },
    orbit: { deg: 120, r: 40, pitch0: 50, pitch1: 8, t: 1.2 }, radio: { hold: 2, perChar: 0.04, cap: 8 },
    help: { on: 0.25, off: 0.25, total: 10 }, meter: { burn: 2, refill: 4.5, lockout: false }
  };
  // the cockpit's palette ("Cockpit" neutral); a skin's look3d.cockpit replaces any key
  var LOOK = {
    frame: '#353B48', frameLit: '#5E687B', frameDark: '#181B22', nose: '#404857', noseLit: '#7A869C', noseDark: '#242932',
    shell: '#363C49', console: '#1E222B', strip: '#4DE8E0', lamp: '#FFB547', caution: '#FFB547', mfd: '#4DE8E0', mfdBg: '#06171A',
    pod: '#16323A', podScreen: '#5FD8E8', wedge: '#E8EEF6', glass: '#9FC4E8'
  };
  var EVENT_COL = { done: '#4ADE80', failed: '#FF5A5F', needs: '#FFB547' };
  // the region's light where no space module writes ctx.sky yet: the nebula's middle colour per purpose
  var REGION_TINT = {
    code: '#1FB5A8', notes: '#C0569C', data: '#C77A2E', media: '#3FD6A0', reference: '#B89A5A', config: '#4C9CD0',
    shared: '#3FB0C8', archive: '#7A5A30', unsorted: '#3A4250'
  };
  var CAPTION = { keys: 'Cockpit view · Esc pauses · I returns to the map', touch: 'Cockpit view · Back returns to the map' };
  var EXIT = { flash: 0.15, rise: 0.8, tally: 1.2 }, ENTER = { dive: 1.0, cockpit: 0.35, target: 0.8 }, REDUCED_T = 0.3;
  var MATCH_T = 0.25, FLYTO_T = 0.8, FLYTO_WARP = 1500, CLEAR_K = 1.05, EVENT_T = 2.5, IDLE_FOR = 3, SPILL_T = 0.3, SPILL_GAP = 3;
  // the sun counts as a body of its near field for the clearance rule: its glare sprite's radius in sun radii (world3d/
  // space.js draws the glare 8 r wide; world3d/core.js launches a ship resting inside it before the entry)
  var SUN_NEAR = 4;

  function merge(a, b) {
    var out = {};
    Object.keys(a).forEach(function (k) {
      var v = a[k], o = b && b[k];
      if (v && typeof v === 'object' && !Array.isArray(v)) out[k] = merge(v, o && typeof o === 'object' ? o : {});
      else out[k] = (o !== undefined && o !== null && (typeof o === typeof v || Array.isArray(v) === Array.isArray(o))) ? o : v;
    });
    Object.keys(b || {}).forEach(function (k) { if (!(k in out)) out[k] = b[k]; });
    return out;
  }
  function num(v, d) { var x = Number(v); return isFinite(x) ? x : d; }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function smooth(t) { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); }
  function cubic(t) { t = clamp(t, 0, 1); return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function wrap(a) { return Math.atan2(Math.sin(a), Math.cos(a)); }
  function lerpAngle(a, b, t) { return a + wrap(b - a) * t; }
  function ease(k, dt) { return 1 - Math.exp(-k * Math.max(0, dt)); }
  function mixHex(a, b, t) { var p = hexRgb(a), q = hexRgb(b); return 'rgb(' + p.map(function (v, i) { return Math.round(v + (q[i] - v) * t); }).join(',') + ')'; }
  function hexRgb(h) { var v = parseInt(String(h || '#000').replace('#', ''), 16) || 0; return [v >> 16 & 255, v >> 8 & 255, v & 255]; }
  function r3(v) { return Math.round(v * 1000) / 1000; }
  function r1(v) { return Math.round(v * 10) / 10; }
  // the log-space move (video-x6: dist = exp(lerp(log near, log far, ease))) with a trapezoid ease: it gathers speed over
  // the first fifth, runs at one rate through the middle three fifths (successive distances keep one ratio there) and
  // settles over the last fifth
  function trapezoid(t) {
    t = clamp(t, 0, 1);
    var a = 0.2, v = 1 / (1 - a);                         // peak rate: the area under the speed curve is 1
    if (t < a) return v * t * t / (2 * a);
    if (t > 1 - a) { var r = 1 - t; return 1 - v * r * r / (2 * a); }
    return v * (t - a / 2);
  }
  function logLerp(a, b, s) { return Math.exp(lerp(Math.log(Math.max(1e-3, a)), Math.log(Math.max(1e-3, b)), s)); }
  FH.trapezoid = trapezoid; FH.logLerp = logLerp;

  // the numbers this skin flies with: neutral, then the skin's feel3d over them; help and lock in seconds either way
  function resolveFeel(skin3d) {
    var f = merge(NEUTRAL, (skin3d && skin3d.feel3d) || {});
    var h = f.help || {};
    if (h.tick) f.helpS = { period: (num(h.on, 4) + num(h.off, 4)) * num(h.tick, 1 / 30), on: num(h.on, 4) * num(h.tick, 1 / 30), total: num(h.ticks, 300) * num(h.tick, 1 / 30) };
    else f.helpS = { period: num(h.on, 0.25) + num(h.off, 0.25), on: num(h.on, 0.25), total: num(h.total, 10) };
    f.lockS = { dwell: num(f.lock.dwell, 0) || 0, hold: num(f.lock.hold, 0) || 0, cone: num(f.lock.cone, 10) };
    if (!f.lockS.dwell && !f.lockS.hold) f.lockS.dwell = 0.8;
    f.look = merge(LOOK, ((skin3d && skin3d.look3d) || {}).cockpit || {});
    return f;
  }

  // ---------------------------------------------------------------- session state
  var S = null;                 // this entry's state (null when off)
  var entries = 0;              // entries this page session (the establishing orbit and caption: the first only)
  var V = null;                 // scratch vectors
  C.provides('camera', 'transition');

  function settings(ctx) {
    var get = function (k, d) { try { return ctx.page && ctx.page.settings ? ctx.page.settings.get(k, d) : d; } catch (e) { return d; } };
    var glassRaw = get('glass', ''), glass = glassRaw === '' || glassRaw == null || !isFinite(Number(glassRaw)) ? Math.round(num(S.feel.cockpit.glass, 0.2) * 100) : clamp(Math.round(Number(glassRaw)), 0, 100);
    var aspect = String(get('hudAspect', 'expand'));
    var view = String(get('view3d', 'cockpit'));
    return {
      glass: glass, aspect: aspect === '16:9' || aspect === '4:3' ? aspect : 'expand',
      reduceFlashing: String(get('reduceFlashing', 'off')) === 'on', comfort: String(get('comfort', 'off')) === 'on',
      view: view === 'chase' ? 'chase' : 'cockpit'
    };
  }

  // ---------------------------------------------------------------- the followed workflow and its events (rev diffs)
  function steps(wf) { return (wf && Array.isArray(wf.steps) ? wf.steps : []).filter(function (s) { return s && typeof s === 'object'; }); }
  function curIndex(wf) {
    var st = steps(wf), cur = Array.isArray(wf && wf.current) ? wf.current : [], i;
    for (i = 0; i < st.length; i++) if (cur.indexOf(st[i].key) >= 0) return i;
    for (i = 0; i < st.length; i++) if (st[i].state === 'working' || st[i].state === 'needs_you') return i;
    for (i = st.length - 1; i >= 0; i--) if (st[i].state === 'done') return Math.min(st.length - 1, i);
    return st.length ? 0 : -1;
  }
  function followed(w) {
    var list = (w.workflows || []).filter(function (x) { return x && typeof x === 'object' && x.id; }), sel = w.selectedWorkflow, f = null;
    if (sel) f = list.filter(function (x) { return x.id === sel; })[0] || null;
    if (!f) {
      var live = list.filter(function (x) { return x.state === 'working' || x.state === 'needs_you'; });
      live.sort(function (a, b) { return String(b.updated || '').localeCompare(String(a.updated || '')); });
      f = live[0] || null;
    }
    return f;
  }
  function placeOf(w, st) {
    if (!st) return '';
    if (st.place) return String(st.place);
    var p = (w.planets || []).filter(function (x) { return x.uid === st.planet_uid; })[0];
    return p ? String(p.label) : '';
  }
  function describe(w, wf) {
    if (!wf) return null;
    var st = steps(wf), i = curIndex(wf), cur = i >= 0 ? st[i] : null;
    var planet = cur ? cur.planet_uid || null : null;
    var agents = (w.craft || []).filter(function (c) { return c.role !== 'carrier' && planet && c.planet === planet; });
    var working = agents.filter(function (c) { return String(c.state).indexOf('work') >= 0; })[0] || agents[0] || null;
    return {
      id: String(wf.id), title: String(wf.title || 'Workflow'), sentence: String(wf.sentence || ''), goal: String(wf.goal || ''),
      state: String(wf.state || ''), done: Number.isInteger(wf.done) ? wf.done : st.filter(function (s) { return s.state === 'done'; }).length,
      total: Number.isInteger(wf.total) ? wf.total : st.length, index: i, step: cur, label: cur ? String(cur.label || cur.key) : '',
      place: placeOf(w, cur), planet: planet, says: cur ? String(cur.says || '') : '', agent: working ? working.uid : null,
      agentLabel: working ? String(working.label || '') : '', agentColour: working ? working.colour || null : null,
      steps: st.map(function (s) { return { key: s.key, label: String(s.label || s.key), state: s.state, planet: s.planet_uid || null, place: placeOf(w, s), artifact: s.artifact || null, kind: s.kind || '' }; }),
      agents: (w.craft || []).filter(function (c) { return c.role !== 'carrier' && st.some(function (s) { return s.planet_uid && s.planet_uid === c.planet; }); }).map(function (c) { return c.uid; }).slice(0, 3)
    };
  }
  function emit(ev) {
    FH.seq = (FH.seq || 0) + 1; ev.seq = FH.seq; ev.t = S ? S.t : 0;
    FH.events.push(ev); if (FH.events.length > 40) FH.events.shift();
    return ev;
  }
  function scan(w) {
    if (S.rev === w.rev && S.scanned) return;
    S.rev = w.rev;
    var first = !S.scanned; S.scanned = true;
    var wf = followed(w), d = describe(w, wf), prev = S.prevWf || {}, prevFollow = S.prevFollowId;
    FH.follow = d;
    var list = (w.workflows || []).filter(function (x) { return x && x.id; }), next = {};
    list.forEach(function (x) {
      var m = {}; steps(x).forEach(function (s) { m[s.key] = { state: s.state, says: String(s.says || '') }; });
      next[x.id] = { state: x.state, steps: m, index: curIndex(x) };
    });
    if (!first && d) {
      var was = prev[d.id], now = next[d.id];
      if (prevFollow !== d.id) emit({ kind: 'start', wf: d.id, title: d.title });
      else if (was && now) {
        steps(wf).forEach(function (s, i) {
          var o = was.steps[s.key], ns = s.state;
          if (!o || o.state === ns) {
            if (o && ns === 'working' && String(s.says || '') && String(s.says || '') !== o.says)
              emit({ kind: 'chatter', wf: d.id, priority: 0, who: d.agentLabel || d.title, colour: d.agentColour, text: String(s.says) });
            return;
          }
          var label = String(s.label || s.key), place = placeOf(w, s);
          if (ns === 'done') emit({ kind: 'done', wf: d.id, title: d.title, step: label, stepIndex: i + 1, total: d.total, place: place, planet: s.planet_uid, priority: 1, who: d.title, text: 'Done: ' + label + (place ? ' in ' + place : '') });
          else if (ns === 'failed') emit({ kind: 'failed', wf: d.id, title: d.title, step: label, stepIndex: i + 1, total: d.total, place: place, planet: s.planet_uid, priority: 3, who: d.title, text: label + ' failed' + (place ? ' in ' + place : '') + (s.why ? ': ' + String(s.why) : '') });
          else if (ns === 'needs_you') emit({ kind: 'needs', wf: d.id, title: d.title, step: label, stepIndex: i + 1, total: d.total, place: place, planet: s.planet_uid, priority: 2, who: d.title, text: label + ' needs you' + (place ? ' in ' + place : '') });
        });
        if (now.index !== was.index && d.step) emit({ kind: 'step', wf: d.id, title: d.title, step: d.label, stepIndex: d.index + 1, total: d.total, place: d.place, planet: d.planet, priority: 1, who: d.title, text: 'Step ' + (d.index + 1) + ' of ' + d.total + ': ' + d.label + (d.place ? ' in ' + d.place : '') });
        if (now.state === 'done' && was.state !== 'done') { emit({ kind: 'complete', wf: d.id, title: d.title, total: d.total, planet: lastPlanet(d), priority: 2, who: d.title, text: d.title + ' is done: ' + d.done + ' of ' + d.total + ' steps' }); S.finished++; }
      }
    }
    // problems that are new since the last rev (only on data), and the agents that start or stop work
    var foes = {}; (w.foes || []).forEach(function (f) { if (f && f.uid) foes[f.uid] = f; });
    if (!first) Object.keys(foes).forEach(function (uid) {
      if (S.prevFoes[uid]) return;
      var f = foes[uid], p = (w.planets || []).filter(function (x) { return x.uid === f.parent; })[0];
      emit({ kind: 'problem', priority: 3, who: p ? p.label : 'Problem', planet: f.parent, text: 'New problem' + (p ? ' at ' + p.label : '') + ': ' + String(f.label || 'a contact') });
    });
    var craft = {}; (w.craft || []).forEach(function (c) { if (c && c.uid && c.role !== 'carrier') craft[c.uid] = c; });
    if (!first) Object.keys(craft).forEach(function (uid) {
      var c = craft[uid], o = S.prevCraft[uid];
      if (o && o.state !== c.state) emit({ kind: 'chatter', priority: 0, who: c.label, colour: c.colour, agent: uid, text: c.label + ': ' + (String(c.state).indexOf('work') >= 0 ? 'working' : c.state || 'idle') + (c.planet ? ' at ' + planetLabel(w, c.planet) : '') });
    });
    S.prevWf = next; S.prevFollowId = d ? d.id : null; S.prevFoes = foes; S.prevCraft = craft;
  }
  function planetLabel(w, uid) { var p = (w.planets || []).filter(function (x) { return x.uid === uid; })[0]; return p ? p.label : String(uid || ''); }
  function lastPlanet(d) { for (var i = d.steps.length - 1; i >= 0; i--) if (d.steps[i].planet) return d.steps[i].planet; return d.planet; }

  // ---------------------------------------------------------------- the cockpit pass (own scene, own camera, depth cleared)
  // idea from web-flight-simulator (NOTICE, ideas only): the cockpit is pinned to the camera and drawn in a second pass after the world, with
  // the depth cleared, so the world never cuts into it (main.js:171-177, 729-763). Built in camera space at depth 1: a
  // point at NDC (x, y) and depth z sits at (x th a z, y th z, -z), th = tan(fov/2), a = aspect; it is rebuilt when the
  // aspect changes.
  function ndc(x, y, z, th, a) { return [x * th * a * z, y * th * z, -z]; }
  var COL3 = null;
  function lin(hex) { COL3 = COL3 || new S.THREE.Color(); COL3.set(hex); return [COL3.r, COL3.g, COL3.b]; }
  function cockpitBuild(ctx) {
    var THREE = ctx.THREE, K = S.cock = { scene: new THREE.Scene(), cam: new THREE.PerspectiveCamera(S.feel.fov, 1, 0.01, 20), group: new THREE.Group(), meshes: [] };
    K.scene.add(K.group);
    var look = S.feel.look;
    // the cockpit's light: a hemisphere tinted by the region's sky (video-o2 #3), a warm key from the upper left, and two
    // point lights the workflow events colour (video-x5 #26)
    K.hemi = new THREE.HemisphereLight(0xbcd0ff, 0x1a1e28, 1.15); K.scene.add(K.hemi);
    K.key = new THREE.DirectionalLight(0xfff1dc, 1.25); K.key.position.set(-0.55, 1, 0.5); K.scene.add(K.key);
    K.spill = [new THREE.PointLight(0xffffff, 0, 3.2, 1.1), new THREE.PointLight(0xffffff, 0, 3.2, 1.1)];
    K.spill[0].position.set(-0.55, -0.15, -0.55); K.spill[1].position.set(0.55, -0.15, -0.55);
    K.spill.forEach(function (l) { K.group.add(l); });
    K.tint = new THREE.Color(0xbcd0ff); K.tintTarget = new THREE.Color(0xbcd0ff);
    K.aspect = 0; K.fov = 0; K.phone = !!ctx.quality.phone;
    K.mfdCanvas = g.document.createElement('canvas'); K.mfdCanvas.width = 1024; K.mfdCanvas.height = 128;
    K.mfdTex = new THREE.CanvasTexture(K.mfdCanvas); K.mfdTex.colorSpace = THREE.SRGBColorSpace; K.mfdTex.anisotropy = 4;
    K.mfdText = ''; K.mfdAt = -1;
    K.wedgeTex = wedgeTexture(THREE);
    K.cautionTex = stripeTexture(THREE, look.caution, '#101216');
    K.screenTex = screenTexture(THREE, look.podScreen, look.pod);
    var side = THREE.DoubleSide;
    K.mats = {
      // every painted face takes per-vertex colours (a gradient per face), flat lit by the tinted hemisphere and the key
      hull: new THREE.MeshLambertMaterial({ vertexColors: true, flatShading: true, side: side }),
      caution: new THREE.MeshLambertMaterial({ map: K.cautionTex, side: side }),
      glow: new THREE.MeshBasicMaterial({ vertexColors: true, side: side }),
      screen: new THREE.MeshBasicMaterial({ map: K.screenTex, side: side }),
      mfd: new THREE.MeshBasicMaterial({ map: K.mfdTex, side: side }),
      wedge: new THREE.MeshBasicMaterial({ map: K.wedgeTex, color: look.wedge, transparent: true, opacity: 0.18, blending: THREE.AdditiveBlending, depthWrite: false, depthTest: false, side: side }),
      glass: new THREE.MeshBasicMaterial({ color: look.glass, transparent: true, opacity: 0.2, depthWrite: false, side: side })
    };
    // every cockpit material sits in three's transparent list, so it draws after the world's own clouds and glows
    Object.keys(K.mats).forEach(function (k) { var m = K.mats[k]; if (!m.transparent) { m.transparent = true; m.opacity = 1; } });
    warpBuild(ctx);
  }
  // soft bands from the apex (v = 0 at the apex, 1 at the far end): bright at the apex, gone at the end, soft sides
  function wedgeTexture(THREE) {
    var c = g.document.createElement('canvas'), w = 64, h = 256; c.width = w; c.height = h;
    var x = c.getContext('2d'), img = x.createImageData(w, h), px = img.data;
    for (var j = 0; j < h; j++) {
      var u = j / (h - 1), along = Math.pow(1 - u, 1.25), bands = 0.7 + 0.3 * Math.cos(u * Math.PI * 6);
      for (var i = 0; i < w; i++) {
        var v = Math.abs(i / (w - 1) - 0.5) * 2, side = 1 - smooth((v - 0.3) / 0.7), streak = 0.8 + 0.2 * Math.cos(i / (w - 1) * Math.PI * 5);
        var a = clamp(along * bands * side * streak, 0, 1), o = (j * w + i) * 4;
        px[o] = px[o + 1] = px[o + 2] = 255; px[o + 3] = Math.round(a * 255);
      }
    }
    x.putImageData(img, 0, 0);
    var t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
  }
  // the caution strip's diagonal bands (the cockpit's amber caution strip; the private skin's yellow and black)
  function stripeTexture(THREE, a, b) {
    var c = g.document.createElement('canvas'); c.width = 128; c.height = 32;
    var x = c.getContext('2d'); x.fillStyle = b; x.fillRect(0, 0, 128, 32); x.fillStyle = a;
    for (var i = -2; i < 10; i++) { x.beginPath(); x.moveTo(i * 16, 32); x.lineTo(i * 16 + 8, 32); x.lineTo(i * 16 + 24, 0); x.lineTo(i * 16 + 16, 0); x.closePath(); x.fill(); }
    var t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
  }
  // a console screen: a lit gradient, a few text rows and a scan band
  function screenTexture(THREE, lit, deep) {
    var c = g.document.createElement('canvas'); c.width = 128; c.height = 48;
    var x = c.getContext('2d'), gr = x.createLinearGradient(0, 0, 0, 48);
    gr.addColorStop(0, lit); gr.addColorStop(1, deep); x.fillStyle = gr; x.fillRect(0, 0, 128, 48);
    x.fillStyle = 'rgba(255,255,255,0.55)'; [10, 18, 26, 34].forEach(function (y, i) { x.fillRect(10, y, 70 - i * 12, 3); });
    x.fillStyle = 'rgba(255,255,255,0.18)'; x.fillRect(0, 20, 128, 6);
    x.strokeStyle = 'rgba(0,0,0,0.5)'; x.lineWidth = 3; x.strokeRect(1.5, 1.5, 125, 45);
    var t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
  }
  // a flat polygon in NDC (x, y[, depth]) at depth z, triangulated as a fan (convex shapes only), painted top -> bottom
  // in cols [top, bottom] (vertex colours, so one material draws every face), or given uvs for a textured material
  function poly(THREE, pts, z, th, a, mat, order, name, cols, uvs) {
    var pos = [], col = [], uv = [], ys = pts.map(function (p) { return p[1]; }), y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    var ct = cols ? lin(cols[0]) : null, cb = cols ? lin(cols[1] || cols[0]) : null;
    var paint = function (p) { var t = y1 > y0 ? (p[1] - y0) / (y1 - y0) : 1; col.push(cb[0] + (ct[0] - cb[0]) * t, cb[1] + (ct[1] - cb[1]) * t, cb[2] + (ct[2] - cb[2]) * t); };
    for (var i = 1; i < pts.length - 1; i++) [0, i, i + 1].forEach(function (k) {
      var p = pts[k]; pos.push.apply(pos, ndc(p[0], p[1], p[2] != null ? p[2] : z, th, a));
      if (cols) paint(p);
      if (uvs) uv.push(uvs[k][0], uvs[k][1]);
    });
    var geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    if (cols) geo.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
    if (uvs) geo.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    geo.computeVertexNormals();
    var m = new THREE.Mesh(geo, mat); m.renderOrder = order; m.frustumCulled = false; m.name = name || '';
    S.cock.group.add(m); S.cock.meshes.push(m); return m;
  }
  // the shell's top edge: the line from the screen edge at yEdge down to (xi, ys), extended past the edge (the group rolls)
  function edgeY(x, yEdge, xi, ys) { var s = (ys - yEdge) / Math.max(1e-3, 1 - xi); return yEdge + (1 - Math.abs(x)) * s; }
  // the cockpit (the look sheet's "Cockpit" row; comps/3d-planet-workflow.png): at the bottom 15% of the view
  // (a phone 12%) a nose rising to a point in the middle with a caution strip, the side shells sloping from the screen
  // edges down to the console, the console with two side screens, amber lamps and the MFD; at the top a slim canopy rim,
  // two struts and the glass between them; two light wedges from the lower corners. Centre parts are sized in units of
  // the height (x / aspect), so a phone's nose is not a needle; edge parts run past the screen edges (the group rolls).
  function cockpitShape(ctx, aspect, fov) {
    var THREE = ctx.THREE, K = S.cock, M = K.mats, L = S.feel.look, th = Math.tan(fov * D2R / 2), a = aspect;
    K.meshes.forEach(function (m) { K.group.remove(m); if (m.geometry) m.geometry.dispose(); });
    K.meshes = [];
    var X = function (h) { return h / a; };
    var share = K.phone ? 0.115 : 0.145, yTip = -1 + 2 * share, yEdge = yTip + 0.006;
    var yC = K.phone ? -0.915 : -0.89, ys = yC + 0.02, xi = Math.min(0.8, X(0.71)), B = -1.4, E = 1.4;
    var nose = L.nose || L.frame, noseLit = L.noseLit || L.frameLit, noseDark = L.noseDark || L.frameDark;
    var shell = L.shell || L.frame, consoleCol = L.console || L.frameDark;
    // the side shells: a lit top edge strip, then the shell face
    [-1, 1].forEach(function (sx) {
      var outer = [sx * E, edgeY(E, yEdge, xi, ys)], inner = [sx * xi, ys];
      poly(THREE, [outer, inner, [sx * xi, B], [sx * E, B]], 1.0, th, a, M.hull, 10, 'shell', [shell, L.frameDark]);
      var lip = 0.018;
      poly(THREE, [[outer[0], outer[1] + lip], [inner[0], inner[1] + lip], inner, outer], 0.995, th, a, M.glow, 12, 'shell-strip', [L.strip, L.strip]);
      // a vent row on each shell (panel lines, the decal rule: no outlines, a few hard strips)
      for (var v = 0; v < 4; v++) {
        var vx0 = sx * (xi + 0.07 + v * 0.055), vx1 = vx0 + sx * 0.032, vy = edgeY(vx0, yEdge, xi, ys) - 0.045;
        poly(THREE, [[vx0, vy], [vx1, vy - 0.006], [vx1, vy - 0.03], [vx0, vy - 0.024]], 0.99, th, a, M.hull, 11, 'vent', [L.frameDark, '#000000']);
      }
      // amber lamps at the console's corners
      var lx = sx * (xi - 0.012);
      poly(THREE, [[lx - 0.008, yC - 0.012], [lx + 0.008, yC - 0.012], [lx + 0.008, yC - 0.03], [lx - 0.008, yC - 0.03]], 0.97, th, a, M.glow, 15, 'lamp', [L.lamp, L.lamp]);
    });
    // the console across the middle, its top lip lit
    poly(THREE, [[-xi - 0.01, yC], [xi + 0.01, yC], [xi + 0.01, B], [-xi - 0.01, B]], 0.99, th, a, M.hull, 11, 'console', [consoleCol, '#08090c']);
    poly(THREE, [[-xi - 0.01, yC + 0.012], [xi + 0.01, yC + 0.012], [xi + 0.01, yC], [-xi - 0.01, yC]], 0.985, th, a, M.hull, 12, 'console-lip', [L.frameLit, L.frame]);
    // the two side screens (o1 #7 pods): a dark box, a lit screen inside
    var podW = Math.min(0.16, X(0.24)), podC = xi * 0.8, py0 = yC - 0.02, py1 = K.phone ? -0.975 : -0.968;
    [-1, 1].forEach(function (sx) {
      var x0 = sx * (podC - podW / 2), x1 = sx * (podC + podW / 2);
      poly(THREE, [[x0, py0], [x1, py0], [x1, py1], [x0, py1]], 0.975, th, a, M.hull, 13, 'pod', [L.pod, '#05080a']);
      var ix0 = x0 + sx * 0.008, ix1 = x1 - sx * 0.008, uv = sx < 0 ? [[0, 1], [1, 1], [1, 0], [0, 0]] : [[1, 1], [0, 1], [0, 0], [1, 0]];
      poly(THREE, [[ix0, py0 - 0.008], [ix1, py0 - 0.008], [ix1, py1 + 0.008], [ix0, py1 + 0.008]], 0.97, th, a, M.screen, 14, 'pod-screen', null, uv);
    });
    // the nose: a low pyramid from the console up to the tip, lit on the left (the key), a ridge, the caution strip
    var bh = Math.min(0.42, X(0.33)), yB = yC + 0.004, front = [0, yB - 0.035, 0.94];
    var T = [0, yTip, 1.02], BL = [-bh, yB, 0.98], BR = [bh, yB, 0.98];
    poly(THREE, [T, BL, front], 1.0, th, a, M.hull, 16, 'nose-left', [noseLit, nose]);
    poly(THREE, [T, front, BR], 1.0, th, a, M.hull, 16, 'nose-right', [nose, noseDark]);
    poly(THREE, [[0, yTip + 0.004, 1.015], [-0.004, yTip - 0.01, 1.01], [0, front[1] + 0.004, 0.945], [0.004, yTip - 0.01, 1.01]], 0.999, th, a, M.hull, 17, 'nose-ridge', [L.frameLit, noseLit]);
    var cy0 = yTip - 0.022, cy1 = yTip - 0.06, wAt = function (y) { return bh * (yTip - y) / Math.max(1e-3, yTip - yB); };
    poly(THREE, [[-wAt(cy0), cy0, 1.0], [wAt(cy0), cy0, 1.0], [wAt(cy1), cy1, 0.995], [-wAt(cy1), cy1, 0.995]], 0.996, th, a, M.caution, 18, 'caution', null,
      [[0, 1], [1, 1], [1, 0], [0, 0]]);
    // the MFD in the console's face (tilted back: its top edge narrower), the current workflow step at 8 Hz
    var mw = Math.min(0.22, X(0.39)), my0 = yC - 0.024, my1 = K.phone ? -0.99 : -0.994;
    K.mfdMesh = poly(THREE, [[-mw * 0.95, my0], [mw * 0.95, my0], [mw, my1], [-mw, my1]], 0.96, th, a, M.mfd, 19, 'mfd', null, [[0, 1], [1, 1], [1, 0], [0, 0]]);
    // the canopy: a slim top rim, two struts and the glass between them over the top 12% of the view with separate panes drawn last, on both faces, without depth writes; opacity follows the glass setting
    var gy = 1 - 0.24, rim = 0.016, st = Math.max(0.3, Math.min(0.5, X(0.62)));
    poly(THREE, [[-E, 1.04], [E, 1.04], [E, 1 - rim], [-E, 1 - rim]], 1.0, th, a, M.hull, 9, 'rim', [L.frame, L.frameDark]);
    [-1, 1].forEach(function (sx) {
      poly(THREE, [[sx * st, 1 - rim * 0.5], [sx * (st + 0.026), 1 - rim * 0.5], [sx * (st + 0.13), gy], [sx * (st + 0.104), gy]], 1.0, th, a, M.hull, 9, 'strut', [L.frameLit, L.frame]);
      poly(THREE, [[sx * (st + 0.104), gy + 0.008], [sx * E, gy + 0.02], [sx * E, gy - 0.012], [sx * (st + 0.104), gy - 0.006]], 1.0, th, a, M.hull, 9, 'sill', [L.frameLit, L.frameDark]);
    });
    K.glass = poly(THREE, [[-E, 1 - rim], [E, 1 - rim], [E, gy], [-E, gy]], 1.01, th, a, M.glass, 40, 'glass');
    // the light wedges (video-o1 #6; comp: apex near (20%, 100%), fanning to the screen edge between y 30% and 80%)
    K.wedges = [];
    [-1, 1].forEach(function (sx) {
      var m = poly(THREE, [[sx * 0.56, -1.03], [sx * 0.66, -1.03], [sx * 1.04, -0.62], [sx * 1.04, 0.42]], 1.0, th, a, M.wedge, 5, 'wedge', null,
        [[0.35, 0], [0.65, 0], [1, 1], [0, 1]]);
      K.wedges.push(m);
    });
    K.top = yTip; K.gy = gy; K.aspect = aspect; K.fov = fov;
    // the canvas point a pane covers (outside the strut, half way down the band), for the glass check
    K.glassNdc = [-Math.min(0.9, st + 0.3), (1 - rim + gy) / 2];
  }
  function cockpitRender(renderer) {
    var K = S && S.cock; if (!K || !K.visible) return;
    var auto = renderer.autoClear;
    renderer.autoClear = false;
    renderer.clearDepth();
    renderer.render(K.scene, K.cam);
    renderer.autoClear = auto;
  }
  function cockpitUpdate(ctx, dt, show, rise) {
    var K = S.cock, fov = S.feel.fov;
    var sz = viewSize(), aspect = sz.w / sz.h;
    if (Math.abs(aspect - K.aspect) > 1e-3 || K.fov !== fov) cockpitShape(ctx, aspect, fov);
    K.cam.fov = fov; K.cam.aspect = aspect; K.cam.updateProjectionMatrix();
    K.visible = !!show;
    FH.cockShare = K.visible ? clamp((K.top + 1) / 2, 0, 1) : 0;   // the share of the screen's height the cockpit covers (the HUD keeps above it)
    // the cockpit rises from below on entry (rise 0 -> 1) and rolls 0.45 of the view's roll
    var roll = (S.viewRoll || 0) * 0.45;
    K.group.position.set(0, -(1 - rise) * 0.55 * Math.tan(fov * D2R / 2), 0);
    K.group.rotation.set(0, 0, roll * D2R);
    // the glass: its opacity is the setting (alpha follows the opacity option)
    var gl = S.set.glass / 100;
    K.mats.glass.opacity = gl; K.glass.visible = gl > 0;
    // the tint: the hemisphere light follows the region's sky over about 2 s (video-o2 #3)
    var tint = skyTint(ctx);
    K.tintTarget.set(tint);
    K.tint.lerp(K.tintTarget, ease(1.6, dt));
    K.hemi.color.copy(K.tint);
    var a = K.tint, b = K.tintTarget;
    S.tintDelta = Math.hypot((a.r - b.r) * 255, (a.g - b.g) * 255, (a.b - b.b) * 255);
    // the spill (video-x5 #26): up to the event colour and back over 0.3 s
    var sp = S.spill;
    if (sp.on) {
      sp.age += dt;
      var k = sp.age < SPILL_T / 3 ? sp.age / (SPILL_T / 3) : Math.max(0, 1 - (sp.age - SPILL_T / 3) / (SPILL_T * 2 / 3));
      K.spill.forEach(function (l) { l.color.set(sp.colour); l.intensity = 3.2 * k; });
      if (sp.age >= SPILL_T) { sp.on = false; K.spill.forEach(function (l) { l.intensity = 0; }); }
    }
    // the MFD at 8 Hz: the current step of the followed workflow, else the locked target, else the speed
    if (S.t - K.mfdAt >= 0.125) { K.mfdAt = S.t; mfdDraw(ctx); }
  }
  function mfdText() {
    var f = FH.follow;
    if (f && f.total) return f.title + ' · step ' + Math.max(1, f.index + 1) + ' of ' + f.total;
    var L = FH.lock;
    if (L && L.state === 'locked' && L.label) return 'Locked · ' + L.label;
    return 'No workflow followed';
  }
  function mfdDraw(ctx) {
    var K = S.cock, text = mfdText(), look = S.feel.look, f = FH.follow;
    var key = text + '|' + (f ? f.done + '/' + f.total + ':' + f.index : '');
    if (key === K.mfdKey && K.mfdDrawn) return;
    K.mfdText = text; K.mfdKey = key; K.mfdDrawn = true;
    var c = K.mfdCanvas, x = c.getContext('2d'), W = c.width, H = c.height;
    x.setTransform(1, 0, 0, 1, 0, 0);
    x.fillStyle = look.mfdBg; x.fillRect(0, 0, W, H);
    var gr = x.createLinearGradient(0, 0, 0, H); gr.addColorStop(0, 'rgba(255,255,255,0.10)'); gr.addColorStop(0.5, 'rgba(255,255,255,0)'); x.fillStyle = gr; x.fillRect(0, 0, W, H);
    x.strokeStyle = look.mfd; x.lineWidth = 6; x.strokeRect(5, 5, W - 10, H - 10);
    x.globalAlpha = 0.45; x.lineWidth = 2; x.strokeRect(16, 16, W - 32, H - 32); x.globalAlpha = 1;
    // a step track under the words: one cell per step in its state's brightness
    var steps = f && f.steps ? f.steps : [], tw = W - 80, cw = steps.length ? tw / steps.length : 0;
    steps.forEach(function (s, i) {
      x.globalAlpha = s.state === 'done' ? 1 : i === f.index ? 0.75 : 0.25; x.fillStyle = look.mfd;
      x.fillRect(40 + i * cw + 3, H - 32, cw - 6, 8);
    });
    x.globalAlpha = 1;
    var hi = mixHex(look.mfd, '#FFFFFF', 0.45);
    x.fillStyle = hi; x.font = 'italic 600 54px "Barlow Condensed","Arial Narrow",sans-serif'; x.textBaseline = 'middle'; x.textAlign = 'center';
    var t = text.toUpperCase(), maxW = W - 64; while (x.measureText(t).width > maxW && t.length > 4) t = t.slice(0, -2);
    if (t !== text.toUpperCase()) t = t.slice(0, -1) + '…';
    x.save(); x.translate(W / 2, steps.length ? H / 2 - 10 : H / 2); x.transform(1, 0, -0.12, 1, 0, 0);
    x.shadowColor = look.mfd; x.shadowBlur = 10; x.fillText(t, 0, 2); x.restore();
    // scanlines, for the screen's feel
    x.globalAlpha = 0.16; x.fillStyle = '#000'; for (var y = 0; y < H; y += 4) x.fillRect(0, y, W, 2); x.globalAlpha = 1;
    K.mfdTex.needsUpdate = true;
  }
  function skyTint(ctx) {
    var sk = ctx.sky;
    if (sk && sk.tint != null) return sk.tint;
    return REGION_TINT[S.region] || REGION_TINT.unsorted;
  }
  // the region: the purpose of the planet nearest the ship (the core's rule), read straight from the world
  function region(w) {
    var sp = w.ship || {}, best = null, bd = Infinity;
    (w.planets || []).forEach(function (p) { var d = Math.hypot(p.x - sp.x, p.y - sp.y) - p.r; if (d < bd) { bd = d; best = p; } });
    return best && REGION_TINT[best.purpose] ? best.purpose : 'unsorted';
  }

  // ---------------------------------------------------------------- warp streaks (nebreck WarpTunnel.ts:29-77, MIT)
  /* port: nebreck (MIT) */
  // 170 streaks parented to the camera on a tunnel around the view axis; progress drives their opacity and speed. They
  // show only while the shader warm-up is still running at the dive's start, and during a long "Fly to".
  function warpBuild(ctx) {
    var THREE = ctx.THREE, n = ctx.quality.phone ? 90 : 170, pos = new Float32Array(n * 6), seeds = [];
    for (var i = 0; i < n; i++) {
      var a = (i / n) * TAU + Math.sin(i * 12.9898) * 0.3, r = 0.35 + ((i * 7919) % 97) / 97 * 0.9, z = ((i * 104729) % 1000) / 1000;
      seeds.push({ a: a, r: r, z: z, len: 0.12 + ((i * 31) % 17) / 17 * 0.25 });
    }
    var geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    var mat = new THREE.LineBasicMaterial({ color: 0xcfc8ff, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, depthTest: false });
    var lines = new THREE.LineSegments(geo, mat); lines.frustumCulled = false; lines.renderOrder = 35; lines.visible = false;
    S.cock.scene.add(lines);
    S.warp = { lines: lines, seeds: seeds, k: 0, on: false, phase: 0 };
  }
  function warpUpdate(dt, on) {
    var W = S.warp; if (!W) return;
    W.k += ((on ? 1 : 0) - W.k) * ease(on ? 10 : 6, dt);
    W.lines.visible = W.k > 0.01;
    if (!W.lines.visible) return;
    W.phase += dt * (1.2 + 2.4 * W.k);
    var p = W.lines.geometry.attributes.position.array, th = Math.tan(S.feel.fov * D2R / 2);
    W.seeds.forEach(function (s, i) {
      var z = 2.5 - ((s.z + W.phase) % 1) * 2.4, r = s.r * th * 2.2;
      var x = Math.cos(s.a) * r, y = Math.sin(s.a) * r * 0.75;
      p[i * 6] = x; p[i * 6 + 1] = y; p[i * 6 + 2] = -z; p[i * 6 + 3] = x; p[i * 6 + 4] = y; p[i * 6 + 5] = -(z + s.len * (0.5 + 2 * W.k));
    });
    W.lines.geometry.attributes.position.needsUpdate = true;
    W.lines.material.opacity = 0.75 * W.k;
  }

  // ---------------------------------------------------------------- the 2D layer (comps/3d-transition.png)
  // The 2D map's own frame (the page's canvas as the matched first frame drew it) laid on the plane under the 3D bodies:
  // the first frame is the 2D one; the dive tilts it into perspective (the map's rings, plates and paint), fades it (alpha
  // 0.55 at 0.8 s, gone at the dive's end) and dissolves its edges into the sky; hidden from the orbit on.
  var LAYER_VERT = 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }';
  var LAYER_FRAG = ['uniform sampler2D uMap; uniform float uOp; uniform float uEdge; varying vec2 vUv;',
    'void main(){ vec4 c = texture2D(uMap, vUv); float r = length((vUv - 0.5) * 2.0);',
    '  gl_FragColor = vec4(c.rgb, uOp * (1.0 - uEdge * smoothstep(0.45, 1.0, r)));',
    '  #include <colorspace_fragment>', '}'].join('\n');
  function layerBuild(ctx) {
    var m2 = ctx.page && typeof ctx.page.map2D === 'function' ? ctx.page.map2D() : null, src = m2 && m2.canvas;
    if (!src || !(src.width > 1) || !(m2.w > 0) || !(m2.h > 0)) return;
    var THREE = ctx.THREE, cap = layerCap(ctx), k = Math.min(1, cap / Math.max(src.width, src.height));
    var c = g.document.createElement('canvas'); c.width = Math.max(2, Math.round(src.width * k)); c.height = Math.max(2, Math.round(src.height * k));
    c.getContext('2d').drawImage(src, 0, 0, c.width, c.height);
    var tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace; tex.minFilter = THREE.LinearFilter; tex.generateMipmaps = false;
    var mat = new THREE.ShaderMaterial({ uniforms: { uMap: { value: tex }, uOp: { value: 1 }, uEdge: { value: 0 } }, vertexShader: LAYER_VERT,
      fragmentShader: LAYER_FRAG, transparent: true, depthWrite: false });
    mat.userData.cmOwner = 'camera';
    var geo = new THREE.PlaneGeometry(m2.w, m2.h), mesh = new THREE.Mesh(geo, mat);
    mesh.rotation.x = -Math.PI / 2; mesh.position.set(m2.x + m2.w / 2, -0.5, m2.y + m2.h / 2);
    mesh.renderOrder = -10; mesh.frustumCulled = false; mesh.userData.noStandIn = true; mesh.name = 'cm3d-layer2d';
    ctx.scene.add(mesh);
    S.layer = { mesh: mesh, mat: mat, tex: tex, geo: geo, op: 1, px: [c.width, c.height] };
  }
  // the layer's long side: the 3D drawing buffer's (never more than the screen it is drawn on), under 2048 on a desktop and
  // 1024 on a phone (a phone's 8 MB of textures; a 2532 px canvas copied whole was 7.7 MB on its own)
  function layerCap(ctx) {
    var ph = !!(ctx.quality && ctx.quality.phone), bw = 0, bh = 0;
    try { var gl = ctx.renderer.getContext(); bw = gl.drawingBufferWidth || 0; bh = gl.drawingBufferHeight || 0; } catch (e) { bw = bh = 0; }
    var buf = Math.max(bw, bh);
    return Math.min(ph ? 1024 : 2048, buf > 0 ? Math.max(256, buf) : (ph ? 1024 : 2048));
  }
  function layerSet(op, edge) {
    var L = S.layer; if (!L) return;
    L.op = clamp(op, 0, 1); L.mat.uniforms.uOp.value = L.op; L.mat.uniforms.uEdge.value = clamp(edge, 0, 1); L.mesh.visible = L.op > 0.003;
  }
  function layerDispose() {
    var L = S && S.layer; if (!L) return;
    if (L.mesh.parent) L.mesh.parent.remove(L.mesh);
    L.geo.dispose(); L.mat.dispose(); L.tex.dispose(); S.layer = null;
  }

  // ---------------------------------------------------------------- camera poses
  function viewSize() { return { w: Math.max(1, g.innerWidth || 1), h: Math.max(1, g.innerHeight || 1) }; }
  // the camera at pos looking along the 2D heading yaw, pitched up by pitch, rolled right-wing-down by roll (radians)
  function orient(cam, yaw, pitch, roll) {
    V.e.set(pitch, -(yaw + Math.PI / 2), -roll, 'YXZ');
    cam.quaternion.setFromEuler(V.e);
  }
  // an orbit pose: the camera D away from T, looking at it from the direction yaw, down by down (radians)
  function orbitPose(cam, T, D, yaw, down, roll) {
    var cd = Math.cos(down);
    cam.position.set(T.x - D * cd * Math.cos(yaw), T.y + D * Math.sin(down), T.z - D * cd * Math.sin(yaw));
    orient(cam, yaw, -down, roll || 0);
  }
  function lens(cam, fov, D, offset) {
    var sz = viewSize();
    cam.fov = fov;
    cam.near = Math.max(0.3, Math.min(D * 0.3, 2000)); cam.far = Math.max(90000, D * 2 + 90000);
    if (offset && (Math.abs(offset.x) > 0.5 || Math.abs(offset.y) > 0.5)) cam.setViewOffset(sz.w, sz.h, offset.x, offset.y, sz.w, sz.h);
    else if (cam.view && cam.view.enabled) cam.clearViewOffset();
    cam.updateProjectionMatrix();
  }
  // keep the camera out of every body (three-geospatial PointOfView.ts:86-175: the move is a tween of distance, heading
  // and pitch around a target; ours also never goes under 1.05 of a planet's radius): push it out along the body's normal
  /* port: three-geospatial (MIT) */
  function bodies(w) {
    var st = w.star;
    return (w.planets || []).concat(st && st.r > 0 ? [{ x: st.x, y: st.y, r: st.r * SUN_NEAR, sun: true }] : []);
  }
  function clearOf(w, cam) {
    var min = Infinity, P = cam.position;
    bodies(w).forEach(function (b) {
      if (!(b.r > 0)) return;
      var dx = P.x - b.x, dy = P.y, dz = P.z - b.y, d = Math.hypot(dx, dy, dz);
      if (d < b.r * CLEAR_K) {
        var k = d > 1e-6 ? b.r * CLEAR_K / d : 1;
        if (d <= 1e-6) { dy = 1; k = b.r * CLEAR_K; }
        P.set(b.x + dx * k, dy * k, b.y + dz * k); d = b.r * CLEAR_K;
      }
      min = Math.min(min, d / b.r);
    });
    return min;
  }
  // the dive passes the sun on its way from the 2D frame's centre to the ship: where its eye would enter the sun's near
  // field it rises straight up to that field's surface (x 1.05) and looks down again at the dive's target, so the path
  // stays smooth (the higher of two smooth heights) and the target stays framed; never a sideways snap
  function sunLift(w, cam, T, yaw) {
    var st = w.star; if (!st || !(st.r > 0)) return;
    var R = st.r * SUN_NEAR * CLEAR_K * 1.002, P = cam.position, dx = P.x - st.x, dz = P.z - st.y, h2 = R * R - dx * dx - dz * dz;
    if (h2 <= 0 || P.y * P.y >= h2) return;
    P.y = Math.sqrt(h2);
    var hd = Math.hypot(T.x - P.x, T.z - P.z);
    orient(cam, yaw, -Math.atan2(P.y - T.y, Math.max(1e-6, hd)), 0);
    S.sunLifts = (S.sunLifts || 0) + 1;
  }
  function shipPos(w, out) {
    var sp = w.ship || {}, a = (S.att && S.att.alt) || 0;
    return (out || new S.THREE.Vector3()).set(Number(sp.x) || 0, a, Number(sp.y) || 0);
  }

  // ---------------------------------------------------------------- the cosmetic attitude (R/F, the loop, the roll)
  // the 2D track is the truth; depth adds altitude (R up, F down, at most twice the biggest radius), pitch, the barrel
  // roll's angle and the U-turn's half loop (an Immelmann on the same ground track: alt = R (1 - cos(pi u)), its peak 2R)
  var CLEAR = 14;
  function clearance(w, x, y) {
    var want = 0, floor = 0;
    (w.planets || []).concat(w.star ? [w.star] : []).forEach(function (b) {
      var R0 = (b.r || 0) + CLEAR, Rw = R0 * 1.6, d = Math.hypot(x - b.x, y - b.y);
      if (d < Rw) want = Math.max(want, R0 * (1 - d * d / (Rw * Rw)));
      if (d < R0) floor = Math.max(floor, Math.sqrt(R0 * R0 - d * d));
    });
    return { want: want, floor: floor };
  }
  function attitude(w, dt, ctx) {
    var A = S.att, sp = w.ship || {}, F = S.feel, red = !!w.reduced;
    var hdg = Number(sp.hdg) || 0;
    // the heading's rate gives the turn (the 2D truth, no key reading): the nose leads into it, up to noseLead
    var rate = A.lastHdg == null || dt <= 0 ? 0 : wrap(hdg - A.lastHdg) / dt;
    A.lastHdg = hdg;
    var turnMax = F.turn * D2R, k = clamp(rate / (turnMax || 1), -1, 1);
    if (Math.abs(rate) > turnMax * 3) k = 0;               // a jump (a placement, the U-turn's flip) is not a turn
    A.turn = k;
    A.lead += ((sp.flight3d ? 0 : k * F.noseLead) - A.lead) * ease(6, dt);   // the 3D flight flies where it points
    // pitch: the 3D flight's own (its attitude); else R and F spring the nose up or down and the altitude follows the speed
    var want = (S.input.up ? 1 : 0) - (S.input.down ? 1 : 0);
    if (sp.flight3d) A.pitch = num(sp.pitch, 0);
    else {
      A.pitch += (want * F.pitch - A.pitch) * ease(5, dt);
      if (Math.abs(A.pitch) > F.pitch) A.pitch = Math.sign(A.pitch) * F.pitch;
    }
    var spd = Math.max(0, Number(sp.spd) || 0), maxAlt = 2 * Math.max(60, S.maxR);
    A.cosAlt = clamp(A.cosAlt + Math.sin(A.pitch * D2R) * spd * dt, -maxAlt, maxAlt);
    // the U-turn: the half loop's altitude while it runs, then a glide back to the cosmetic altitude
    if (sp.uturn) {
      if (!A.ut) A.ut = { el: 0, h0: hdg, v: Math.max(spd, F.speed.cruise * 0.84), lift: A.loopLift || 0, side: A.lead < 0 ? -1 : 1 };
      A.ut.el += dt;
      var uT = F.uturnT / 1000, u = clamp(A.ut.el / uT, 0, 1), R = A.ut.v * uT / Math.PI;
      A.ut.R = R; A.ut.u = u;
      A.loopLift = A.ut.lift + R * (1 - Math.cos(Math.PI * u));
      A.loopPitch = 180 * u; A.loopRoll = 180 * smooth((u - 0.5) / 0.5);
    } else {
      if (A.ut) { A.lastR = A.ut.R; A.ut = null; }
      A.loopLift += (0 - A.loopLift) * ease(0.6, dt);
      A.loopPitch = 0; A.loopRoll = 0;
    }
    // the roll: its angle from its own clock; its side from the slip it puts on the 2D track
    if (sp.roll && !red) {
      if (!A.br) A.br = { el: 0, x: Number(sp.x) || 0, y: Number(sp.y) || 0, dir: num(sp.rollDir, 0), h: hdg };   // 3D: Q/E name it
      A.br.el += dt;
      if (!A.br.dir) {
        var lx = -Math.sin(A.br.h), ly = Math.cos(A.br.h), lat = ((Number(sp.x) || 0) - A.br.x) * lx + ((Number(sp.y) || 0) - A.br.y) * ly;
        if (Math.abs(lat) > 0.02) A.br.dir = lat > 0 ? 1 : -1;
      }
      var rT = F.rollT / 1000, ru = A.br.el / rT, full = F.rollDeg;
      // the whole turn by 90% of the roll's time (so its peak is always drawn), then any extra past 360 settles back
      var ang = ru < 0.9 ? full * cubic(ru / 0.9) : lerp(full, 360, smooth((ru - 0.9) / 0.1));
      A.rollDeg = (A.br.dir || 1) * Math.min(full, ang); A.rollPeak = Math.max(A.rollPeak || 0, Math.abs(A.rollDeg));
    } else { A.br = null; A.rollDeg = 0; }
    // over a body the ship rises to clear it (the core's rule), the cosmetic altitude and the loop on top
    var c = clearance(w, Number(sp.x) || 0, Number(sp.y) || 0);
    A.lift = A.lift == null ? c.want : A.lift + (c.want - A.lift) * ease(6, dt);
    if (A.lift < c.floor) A.lift = c.floor;
    A.alt = sp.flight3d ? num(sp.alt, 0) : Math.max(c.floor, A.lift + A.cosAlt + A.loopLift);
    ctx.ship.alt = A.alt;
    ctx.ship.pitch = sp.uturn ? A.loopPitch : A.pitch;
    ctx.ship.rollDeg = sp.uturn ? A.loopRoll : A.rollDeg;
  }

  // ---------------------------------------------------------------- the flight rigs (first-person cockpit / third-person chase)
  function rigPose(w, dt, ctx, snap) {
    var cam = ctx.camera, F = S.feel, A = S.att, sp = w.ship || {}, hdg = Number(sp.hdg) || 0;
    var ship = shipPos(w, V.a), red = !!w.reduced, rig = S.rig;
    var bank = (Number(sp.bank) || 0) * D2R;                    // the 2D bank (radians), right wing down positive
    var cap = red ? 0 : (S.set.comfort ? 8 : 20);
    var fov = F.fov + S.fovKick;
    lens(cam, fov, 50, null);
    cam.near = 0.3; cam.updateProjectionMatrix();
    S.viewRoll = 0;
    S.eyeIn = rig === 'cockpit' || rig === 'noship';
    if (rig === 'event' && S.ev) return eventPose(w, dt, ctx);
    var ut = sp.uturn && A.ut ? A.ut : null, uLook = null;
    if (ut) {
      // the U-turn seen from the ship: the view climbs (pitch up to 60 degrees) and swings round to the new heading with a
      // little roll, so it never passes the vertical (no gimbal flip) and lands level on the reverse heading
      var u = ut.u, s = smooth(u);
      uLook = { yaw: ut.h0 + Math.PI * s * (ut.side || 1), pitch: 60 * D2R * Math.sin(Math.PI * u), roll: (red ? 0 : 30) * D2R * Math.sin(Math.PI * u) };
    }
    if (rig === 'cockpit' || rig === 'noship') {
      var eye = V.b.copy(ship); eye.y += 1.4;
      cam.position.copy(eye);
      var lookYaw = hdg + A.lead * F.cockpit.look * D2R, lookPitch = A.pitch * (sp.flight3d ? 1 : F.cockpit.look) * D2R;
      var roll = clamp(bank * 0.6 * R2D, -cap, cap) * D2R;
      if (A.rollDeg && !S.set.comfort && !red) roll += A.rollDeg * D2R;
      if (uLook) { lookYaw = uLook.yaw; lookPitch = uLook.pitch; roll = uLook.roll; }
      if (rig === 'noship') {
        // o7's no-ship view: a damped slerp toward the heading (1 - exp(-dt 8)), bank 20 degrees at most
        V.e.set(lookPitch, -(lookYaw + Math.PI / 2), -roll, 'YXZ'); V.q.setFromEuler(V.e);
        if (snap || !S.nsQ) S.nsQ = V.q.clone(); else S.nsQ.slerp(V.q, ease(8, dt));
        cam.quaternion.copy(S.nsQ);
      } else orient(cam, lookYaw, lookPitch, roll);
      S.viewRoll = roll * R2D;
      S.camRoll = S.viewRoll;
      S.dist = 0; S.swing = 0; S.rollRate = 0;
      return;
    }
    if (sp.flight3d && !ut) return chaseFlight(w, dt, ctx, snap, ship, fov);
    // chase: the distance by speed (cruise, boost, brake), the eye lag on the heading, the side swing with the turn, the
    // camera roll a third of the ship's bank (neutral) or the nose offset's (the private skin's numbers)
    var ch = F.chase, spd = Math.max(0, Number(sp.spd) || 0), sc = F.speed;
    var want = spd <= sc.brake ? ch.brake : spd <= sc.cruise ? lerp(ch.brake, ch.cruise, (spd - sc.brake) / Math.max(1, sc.cruise - sc.brake))
      : lerp(ch.cruise, ch.boost, clamp((spd - sc.cruise) / Math.max(1, sc.boost - sc.cruise), 0, 1));
    // Preserve the full wingspan in portrait views. The normal speed-based distance remains the floor.
    var own = ctx.audit.models && ctx.audit.models.own, span = own && own.span || 14;
    want = Math.max(want, span / (2 * Math.tan(fov * D2R / 2) * Math.max(0.1, cam.aspect) * 0.68));
    if (snap || S.dist == null || !S.dist) S.dist = want; else S.dist += (want - S.dist) * ease(ch.lag, dt);
    var yawT = uLook ? uLook.yaw : hdg;
    if (snap || S.camYaw == null) S.camYaw = yawT; else S.camYaw = lerpAngle(S.camYaw, yawT, ease(ch.lag, dt));
    var swingT = clamp(A.lead / Math.max(1, F.noseLead), -1, 1) * ch.swing;
    S.swing = snap ? swingT : S.swing + (swingT - S.swing) * ease(ch.lag, dt);
    S.swing = clamp(S.swing, -ch.swing, ch.swing);
    var rollT;
    if (ch.rollRate > 0) {                                  // the nose offset's roll, capped and rate-limited
      rollT = clamp(num(ch.rollCap, 0.3) * A.lead, -num(ch.rollMax, 12), num(ch.rollMax, 12));
      var step = ch.rollRate * dt, d = clamp(rollT - S.camRoll, -step, step);
      var prev = S.camRoll; S.camRoll = snap ? rollT : S.camRoll + d; S.rollRate = dt > 0 ? (S.camRoll - prev) / dt : 0;
    } else {
      rollT = (Number(sp.bank) || 0) * R2D * num(ch.rollCap, 1 / 3);   // the world's bank is in radians
      var p2 = S.camRoll; S.camRoll = rollT; S.rollRate = dt > 0 ? (S.camRoll - p2) / dt : 0;
    }
    if (red) S.camRoll = 0;
    S.camRoll = clamp(S.camRoll, -cap, cap);
    var flightPitch = sp.flight3d ? A.pitch * D2R : 0;
    var f = V.c.set(Math.cos(S.camYaw) * Math.cos(flightPitch), Math.sin(flightPitch), Math.sin(S.camYaw) * Math.cos(flightPitch));
    var r = V.d.set(-Math.sin(S.camYaw), 0, Math.cos(S.camYaw));
    // Physical altitude is a world coordinate. Keep the eye offset relative to the ship's pitch, so climbing high or
    // pointing vertically never moves the ship out of the third-person frame. In the legacy flat flight the eye lags
    // eyeK of the pilot's own climb (R/F; the skin's feel3d.chase.eyeK, 1 = no lag), at most 25 u.
    var eyeUp = sp.flight3d ? Math.cos(flightPitch) * ch.eye : ch.eye;
    var eyeForward = sp.flight3d ? -Math.sin(flightPitch) * ch.eye : 0;
    var lag = sp.flight3d ? 0 : clamp((1 - num(ch.eyeK, 1)) * (A.cosAlt || 0), -25, 25);
    var eyeY = ship.y - lag + eyeUp;
    cam.position.set(ship.x - f.x * S.dist + r.x * S.swing + Math.cos(S.camYaw) * eyeForward,
      eyeY - f.y * S.dist, ship.z - f.z * S.dist + r.z * S.swing + Math.sin(S.camYaw) * eyeForward);
    // The ship can fly around either side of a sphere; its trailing camera needs its own clearance.
    S.minClear = Math.min(S.minClear, clearOf(w, cam));
    var look = V.b.copy(ship).addScaledVector(f, num(ch.look, 40));
    var dx = look.x - cam.position.x, dy = look.y - cam.position.y, dz = look.z - cam.position.z;
    var yaw = Math.atan2(dz, dx), pitch = Math.atan2(dy, Math.hypot(dx, dz));
    orient(cam, yaw, pitch + (uLook ? uLook.pitch * 0.5 : 0), S.camRoll * D2R + (uLook ? uLook.roll : 0));
    S.viewRoll = S.camRoll;
  }
  // the 3D flight's chase (flight-rs's camera; its numbers are for a 170 u ship 300 u behind, scaled here by the skin's
  // chase.cruise over 300): the distance eases to cruise, boost or brake (10% of the way a 30 Hz tick, 40 u a tick at
  // most); the eye chases a point that far behind the ship on its heading and half its pitch, chase.eye above (20% a
  // tick, so it swings out in a turn and trails a little more at speed); it looks at a point 120 u ahead of the nose (50%
  // a tick). The horizon stays level: the view rolls only rollCap x 0.33 of the turn rate (rollMax at most, rollRate
  // degrees a second), never with the ship's bank or roll. A jump (a placement) brings the eye straight there.
  function chaseFlight(w, dt, ctx, snap, ship, fov) {
    var cam = ctx.camera, F = S.feel, A = S.att, sp = w.ship || {}, ch = F.chase, red = !!w.reduced;
    var u = num(ch.cruise, 50) / 300, n = Math.max(0, dt) * 30;
    var want = sp.boost ? ch.boost : sp.brake ? ch.brake : ch.cruise;
    // the whole wingspan in a portrait view: the speed's distance is the floor
    var own = ctx.audit.models && ctx.audit.models.own, span = own && own.span || 14;
    want = Math.max(want, span / (2 * Math.tan(fov * D2R / 2) * Math.max(0.1, cam.aspect) * 0.68));
    if (snap || !S.dist) S.dist = want;
    else S.dist += clamp((want - S.dist) * (1 - Math.pow(0.9, n)), -40 * u * n, 40 * u * n);
    // the camera rides a little higher (ch.lift) so the ship sits below the reticle, the lock bracket and the planet instead of under them
    var hdg = Number(sp.hdg) || 0, p = A.pitch * D2R, hp = p * 0.5, ch0 = Math.cos(hdg), sh0 = Math.sin(hdg);
    var f = V.c.set(ch0 * Math.cos(p), Math.sin(p), sh0 * Math.cos(p));
    var eyeT = V.d.set(ship.x - ch0 * Math.cos(hp) * S.dist, ship.y - Math.sin(hp) * S.dist + num(ch.eye, 9) + num(ch.lift, 11), ship.z - sh0 * Math.cos(hp) * S.dist);
    var lookT = V.b.copy(ship).addScaledVector(f, 120 * u);
    if (snap || !S.eye || S.eye.distanceTo(eyeT) > 8 * Math.max(1, S.dist)) { S.eye = eyeT.clone(); S.look = lookT.clone(); }
    else { S.eye.lerp(eyeT, 1 - Math.pow(0.8, n)); S.look.lerp(lookT, 1 - Math.pow(0.5, n)); }
    var rollT = red ? 0 : clamp(num(ch.rollCap, 0.3) * 0.33 * num(sp.rate, 0), -num(ch.rollMax, 12), num(ch.rollMax, 12));
    var rate = num(ch.rollRate, 0) > 0 ? ch.rollRate : 30, stp = rate * Math.max(0, dt), prev = S.camRoll || 0;
    S.camRoll = snap ? rollT : prev + clamp(rollT - prev, -stp, stp);
    S.rollRate = dt > 0 ? (S.camRoll - prev) / dt : 0;
    S.camRoll = clamp(S.camRoll, -(S.set.comfort ? 8 : 20), S.set.comfort ? 8 : 20);
    cam.position.copy(S.eye);
    S.minClear = Math.min(S.minClear, clearOf(w, cam));
    var dx = S.look.x - cam.position.x, dy = S.look.y - cam.position.y, dz = S.look.z - cam.position.z;
    orient(cam, Math.atan2(dz, dx), Math.atan2(dy, Math.hypot(dx, dz)), S.camRoll * D2R);
    S.viewRoll = S.camRoll; S.swing = 0; S.camYaw = hdg;
  }
  // the event camera (video-o2 #7): the finished planet, a slow 2.5 s orbit, the HUD stays; any key ends it
  function eventPose(w, dt, ctx) {
    var E = S.ev, cam = ctx.camera, p = (w.planets || []).filter(function (x) { return x.uid === E.uid; })[0];
    E.el += dt;
    if (!p || E.el >= EVENT_T) { endEvent(); return rigPose(w, 0, ctx, true); }
    var sz = viewSize(), hfov = 2 * Math.atan(Math.tan(S.feel.fov * D2R / 2) * sz.w / sz.h), D = p.r / (0.34 * Math.tan(hfov / 2));
    var u = E.el / EVENT_T, yaw = E.yaw0 + 40 * D2R * smooth(u);
    V.a.set(p.x, 0, p.y);
    lens(cam, S.feel.fov, D, null);
    orbitPose(cam, V.a, D, yaw, 14 * D2R, 0);
    S.viewRoll = 0; S.camRoll = 0;
  }
  function startEvent(uid) {
    if (S.rig === 'event') return;
    var w = S.world, p = (w.planets || []).filter(function (x) { return x.uid === uid; })[0];
    if (!p) return;
    var sp = w.ship || {};
    S.ev = { uid: uid, el: 0, prev: S.rig, yaw0: Math.atan2(p.y - (Number(sp.y) || 0), p.x - (Number(sp.x) || 0)) };
    S.rig = 'event'; S.evCount++;
  }
  function endEvent() { if (!S.ev) return; S.rig = S.ev.prev || 'cockpit'; S.ev = null; S.nsQ = null; }

  // ---------------------------------------------------------------- the entry and exit timeline
  function tlOpen(name) {
    var t = r3(S.t), e = { name: name, from: t, to: null };
    S.timeline.push(e); return e;
  }
  function tlClose(name) { S.timeline.forEach(function (e) { if (e.name === name && e.to == null) e.to = r3(S.t); }); }
  function tlCloseAll() { S.timeline.forEach(function (e) { if (e.to == null) e.to = r3(S.t); }); }
  // the pose the entry ends in: the chosen rig's, from the ship
  function endPose(w) {
    var sp = w.ship || {}, hdg = Number(sp.hdg) || 0;
    if (S.rig === 'chase') {
      var ch = S.feel.chase, D = ch.cruise;
      return { D: Math.hypot(D, ch.eye), down: Math.atan2(ch.eye, D), yaw: hdg };
    }
    return { D: 0.001, down: 0, yaw: hdg };
  }
  function startEnter(w, ctx) {
    var m = ctx.transition.match() || { x: Number(w.ship.x) || 0, y: Number(w.ship.y) || 0, h: 1200, fov: 4, offset: { x: 0, y: 0 } };
    var first = entries === 1, sp = w.ship || {}, hdg = Number(sp.hdg) || 0;
    S.match = m; S.camOff = { x: m.x - (Number(sp.x) || 0), y: m.y - (Number(sp.y) || 0) };
    tlClose('match');
    var red = !!w.reduced;
    var T = S.tl = { kind: 'enter', el: 0, first: first && !red, red: red, step: '', from: { T: new S.THREE.Vector3(m.x, 0, m.y), D: m.h, down: Math.PI / 2, yaw: -Math.PI / 2, fov: m.fov, off: m.offset } };
    T.orbitR = S.feel.orbit.r; T.orbitDeg = S.feel.orbit.deg; T.orbitSign = S.orbitSign = orbitSide(w, hdg, T.orbitDeg * D2R);
    if (red) { T.parts = [{ name: 'cut', t: 0.05 }]; }
    else {
      T.parts = [{ name: 'dive', t: ENTER.dive }];
      if (T.first) T.parts.push({ name: 'orbit', t: S.feel.orbit.t });
      T.parts.push({ name: 'cockpit', t: ENTER.cockpit });
      if ((ctx.audit.compile || {}).pending > 0) { T.warp = tlOpen('warp'); }
      if (S.cardUid) { T.card = tlOpen('target'); FH.targetCard = { uid: S.cardUid, at: S.t }; }
    }
    T.i = 0; T.pt = 0; T.cur = tlOpen(T.parts[0].name);
    S.minClear = Infinity;
    S.planned = r3(MATCH_T + T.parts.reduce(function (a, q) { return a + q.t; }, 0));
    FH.hudAlpha = 0;
  }
  function finishEnter(ctx, skipped) {
    var T = S.tl; if (!T || T.kind !== 'enter') return;
    tlCloseAll(); layerDispose();
    FH.caption = null; FH.targetCard = null; FH.hudAlpha = 1; FH.entry = null;
    S.tl = null; S.transPhase = null;
    S.cockRise = 1; S.canvasFade(1);
    if (skipped) S.skips++;
    rigPose(S.world, 0, ctx, true);
    ctx.transition.done(S.rig === 'event' ? 'cockpit' : S.rig);
    S.entered = true;
    if (FH.follow) emitCardOnce();
  }
  function emitCardOnce() { if (S.cardShown) return; S.cardShown = true; emit({ kind: 'start', wf: FH.follow.id, title: FH.follow.title, entry: true }); }
  function runEnter(w, dt, ctx) {
    var T = S.tl, cam = ctx.camera, F = S.feel;
    if (ctx.mode !== 'paused') { T.el += dt; T.pt += dt; }
    ctx.transition.step(T.parts[T.i].name);
    // warp: open while the shader warm-up still runs at the dive's start; closes when it is done
    if (T.warp && T.warp.to == null && !((ctx.audit.compile || {}).pending > 0)) T.warp.to = r3(S.t);
    if (T.card && T.card.to == null && T.el >= ENTER.target) { T.card.to = r3(S.t); FH.targetCard = null; }
    warpUpdate(dt, !!(T.warp && T.warp.to == null));
    while (T.pt >= T.parts[T.i].t) {
      T.pt -= T.parts[T.i].t; tlClose(T.parts[T.i].name);
      if (T.parts[T.i].name === 'orbit') orbitMeasure();
      T.i++;
      if (T.i >= T.parts.length) { finishEnter(ctx, false); return; }
      T.cur = tlOpen(T.parts[T.i].name);
      if (T.parts[T.i].name === 'orbit') S.orbitSamples = [];
    }
    var part = T.parts[T.i].name, u = clamp(T.pt / T.parts[T.i].t, 0, 1);
    S.transPhase = part;
    var ship = shipPos(w, V.a.clone()), hdg = Number((w.ship || {}).hdg) || 0;
    var end = endPose(w);
    // the entry's timeline bar (comps/3d-transition.png: "diving to api · 0.8 of 1.25 s"), counted from the match frame
    if (!T.words) T.words = { dive: 'diving to ' + (aheadLabel(w) || 'the ship'), orbit: 'looking round the ship', cockpit: 'into the cockpit', cut: 'into first person' };
    var upTo = MATCH_T; for (var pi = 0; pi <= T.i; pi++) upTo += T.parts[pi].t;
    // the sun's glow over the entry (the hub's ruling, 2026-10-13): the corona comes up over the dive (0.3 at its start, 1 at
    // its end: the 0.8 s frame shows a glowing sun as comps/3d-transition.png does), while the glare and the flare stay 0
    // until the cockpit rise and come up over it; the space lane caps both so neither ever fills the view
    var all = 0, from0 = 0, dive0 = 0, diveT = 0, cock = false;
    T.parts.forEach(function (q) { if (q.name === 'dive') { dive0 = all; diveT = q.t; } if (q.name === 'cockpit') { from0 = all; cock = true; } all += q.t; });
    if (!cock) from0 = Math.max(0, all - ENTER.cockpit);
    S.glareK = T.red ? 1 : smooth((T.el - from0) / Math.max(0.05, all - from0));
    S.coronaK = T.red ? 1 : diveT > 0 && T.el < dive0 + diveT ? 0.3 + 0.7 * smooth((T.el - dive0) / diveT) : 1;
    FH.entry = T.red ? null : { el: r3(MATCH_T + T.el), total: r3(upTo), words: T.words[part] || part, part: part };
    var diveEnd = T.first ? { D: T.orbitR, down: F.orbit.pitch0 * D2R, yaw: hdg + T.orbitSign * T.orbitDeg * D2R } : (S.rig === 'chase' ? end : { D: T.orbitR, down: 8 * D2R, yaw: hdg });
    FH.caption = null;
    S.cockRise = 0; S.eyeIn = false;
    if (part === 'cut') {
      layerSet(0, 1);
      S.canvasFade(clamp(S.t / REDUCED_T, 0, 1));
      rigPose(w, 0, ctx, true); return;
    }
    if (part === 'dive') {
      // port:, the dive: pitch 90 -> 8 degrees, the lens 4 -> 55, the frame's half height in log space (so the
      // zoom reads as one steady push), the heading from north to the ship's, the target from the 2D centre to the ship
      // the frame's height closes in log space late (smoothstep^2.5: still, then fast, then settling into the orbit), so at
      // 0.8 s the view still holds the map round the target at about a third of the 2D scale (comps/3d-transition.png:
      // 820 u out); the target reaches the ship early, so the ship is in frame
      var s = cubic(u), sh = Math.pow(smooth(u), 2.5), fr = T.from, fovS = smooth(clamp(u * 1.25, 0, 1));
      var fov = lerp(fr.fov, F.fov, fovS);
      var H0 = fr.D * Math.tan(fr.fov * D2R / 2), H1 = diveEnd.D * Math.tan(F.fov * D2R / 2), H = logLerp(H0, H1, sh);
      var D = H / Math.tan(fov * D2R / 2);
      var Tt = V.b.copy(fr.T).lerp(ship, smooth(clamp(u * 1.6, 0, 1)));
      var off = { x: fr.off.x * (1 - s), y: fr.off.y * (1 - s) };
      lens(cam, fov, D, off);
      // the 2D layer holds while the map is near its own scale and is gone by 5x (it is the 2D canvas's own pixels)
      layerSet(1 - smooth((H0 / Math.max(1e-6, H) - 1) / 4), smooth(u * 2));
      // the pitch leads the distance (comps/3d-transition.png at 0.8 of 1.25 s: 24 degrees down while still 820 u out), so
      // the map tilts into perspective with its horizon and sky before the camera closes in
      var dyaw = lerpAngle(fr.yaw, diveEnd.yaw, s), sp = smooth(clamp(u * 1.25, 0, 1));
      orbitPose(cam, Tt, D, dyaw, lerp(fr.down, diveEnd.down, sp), 0);
      sunLift(w, cam, Tt, dyaw);
      S.minClear = Math.min(S.minClear, clearOf(w, cam));
      S.diveD = D;
    } else if (part === 'orbit') {
      layerSet(0, 1);
      // the establishing orbit, first entry only moves around the
      // ship at r, pitch from pitch0 down to pitch1; the caption under it, no HUD
      var so = smooth(u);
      lens(cam, F.fov, T.orbitR, null);
      orbitPose(cam, ship, T.orbitR, lerpAngle(diveEnd.yaw, hdg, so), lerp(F.orbit.pitch0, F.orbit.pitch1, so) * D2R, 0);
      S.minClear = Math.min(S.minClear, clearOf(w, cam));
      FH.caption = { text: ctx.quality.phone ? CAPTION.touch : CAPTION.keys };
      var rel = V.c.copy(cam.position).sub(ship);
      S.orbitSamples.push({ yaw: Math.atan2(rel.z, rel.x), r: rel.length(), pitch: Math.asin(clamp(rel.y / Math.max(1e-6, rel.length()), -1, 1)) * R2D });
    } else {
      // the cockpit rises: from the orbit's end (or the dive's) into the rig's pose; the HUD fades in
      layerSet(0, 1);
      var sc = smooth(u), from = T.first ? { D: T.orbitR, down: F.orbit.pitch1 * D2R, yaw: hdg } : diveEnd;
      var Dc = lerp(from.D, end.D, sc), down = lerp(from.down, end.down, sc);
      lens(cam, F.fov, Math.max(1, Dc), null);
      if (S.rig === 'cockpit' || S.rig === 'noship') {
        var eye = V.b.copy(ship); eye.y += 1.4;
        orbitPose(cam, eye, Math.max(0.001, Dc), lerpAngle(from.yaw, end.yaw, sc), down, 0);
      } else orbitPose(cam, ship, Dc, lerpAngle(from.yaw, end.yaw, sc), down, 0);
      cam.near = 0.3; cam.updateProjectionMatrix();
      S.minClear = Math.min(S.minClear, clearOf(w, cam));
      S.cockRise = S.rig === 'cockpit' ? sc : 0;
      S.eyeIn = (S.rig === 'cockpit' || S.rig === 'noship') && sc > 0.6;
      FH.hudAlpha = sc;
    }
  }
  // which way the establishing orbit sweeps (it ends behind the ship, on its heading): the side whose view stays furthest
  // from the sun's bearing all the way round, so the sun is behind or beside the camera, never ahead of it
  function orbitSide(w, hdg, sweep) {
    var st = w.star, sp = w.ship || {};
    if (!st || !(st.r > 0)) return 1;
    var sb = Math.atan2(st.y - (Number(sp.y) || 0), st.x - (Number(sp.x) || 0)), best = 1, bestK = -1;
    [1, -1].forEach(function (sg) {
      var k = Infinity;
      for (var i = 0; i <= 10; i++) k = Math.min(k, Math.abs(wrap(hdg + sg * sweep * (1 - smooth(i / 10)) - sb)));
      if (k > bestK + 1e-6) { bestK = k; best = sg; }
    });
    return best;
  }
  // the planet the entry dives toward: the card's, else the one nearest the nose ahead
  function aheadLabel(w) {
    var sp = w.ship || {}, hdg = Number(sp.hdg) || 0, best = null, bk = 0.5;
    (w.planets || []).forEach(function (p) {
      if (S.cardUid && p.uid === S.cardUid) { best = p; bk = 2; return; }
      var dx = p.x - (Number(sp.x) || 0), dy = p.y - (Number(sp.y) || 0), d = Math.hypot(dx, dy) || 1, k = (dx * Math.cos(hdg) + dy * Math.sin(hdg)) / d;
      if (k > bk) { bk = k; best = p; }
    });
    return best ? String(best.label) : '';
  }
  // the exit's drawn distances read back at even steps of its progress (u 0.2 to 0.8 by 0.05, between the frames' own
  // samples): successive ratios, constant in a log-space move whatever the frame times were
  function logRatios(sm) {
    if (sm.length < 4) return [];
    var at = function (u) {
      for (var i = 1; i < sm.length; i++) if (sm[i][0] >= u) { var a = sm[i - 1], b = sm[i], k = b[0] > a[0] ? (u - a[0]) / (b[0] - a[0]) : 0; return Math.exp(lerp(Math.log(a[1]), Math.log(b[1]), k)); }
      return sm[sm.length - 1][1];
    };
    var out = []; for (var u = 0.2; u < 0.8 - 1e-6; u += 0.05) out.push(r3(at(u + 0.05) / at(u)));
    return out;
  }
  function orbitMeasure() {
    var s = S.orbitSamples || [];
    if (s.length < 2) return;
    var sweep = 0; for (var i = 1; i < s.length; i++) sweep += wrap(s[i].yaw - s[i - 1].yaw);
    S.orbit = { sweep: r1(Math.abs(sweep) * R2D), r: r1(s.reduce(function (a, x) { return a + x.r; }, 0) / s.length),
      pitch0: r1(s[0].pitch), pitch1: r1(s[s.length - 1].pitch), samples: s.length };
  }
  function startExit(w, ctx) {
    var sp = w.ship || {}, red = !!w.reduced, m = S.match || ctx.transition.match();
    var T = S.tl = { kind: 'exit', el: 0, pt: 0, i: 0, red: red };
    var camOff = S.camOff || { x: 0, y: 0 };
    var cam = ctx.camera, ship = shipPos(w, V.a.clone());
    var rel = cam.position.clone().sub(ship), D0 = Math.max(4, rel.length());
    T.from = { D: D0, fov: cam.fov, yaw: Number(sp.hdg) || 0, down: Math.max(0, Math.asin(clamp(rel.y / Math.max(1e-6, rel.length()), -1, 1))) };
    var land = S.landUid ? (w.planets || []).filter(function (p) { return p.uid === S.landUid; })[0] : null;
    T.to = { T: land ? new S.THREE.Vector3(land.x, 0, land.y) : new S.THREE.Vector3((Number(sp.x) || 0) + camOff.x, 0, (Number(sp.y) || 0) + camOff.y),
      D: m && m.h ? m.h : 1200, fov: m && m.fov ? m.fov : 4, off: (m && m.offset) || { x: 0, y: 0 } };
    T.ship0 = ship.clone();
    if (red) T.parts = [{ name: 'fade', t: REDUCED_T }];
    else {
      T.parts = [];
      if (!S.set.reduceFlashing) T.parts.push({ name: 'flash', t: EXIT.flash });
      if (S.finished > 0) { T.parts.push({ name: 'tally', t: EXIT.tally }); FH.tally = { at: S.t, finished: S.finished, steps: S.stepsDone }; }
      T.parts.push({ name: 'rise', t: EXIT.rise });
    }
    S.exitSamples = [];
    T.cur = tlOpen(T.parts[0].name);
  }
  function runExit(w, dt, ctx) {
    var T = S.tl, cam = ctx.camera;
    T.el += dt; T.pt += dt;
    ctx.transition.step(T.parts[T.i].name);
    while (T.pt >= T.parts[T.i].t) {
      T.pt -= T.parts[T.i].t; tlClose(T.parts[T.i].name);
      T.i++;
      if (T.i >= T.parts.length) { finishExit(ctx); return; }
      T.cur = tlOpen(T.parts[T.i].name);
    }
    var part = T.parts[T.i].name, u = clamp(T.pt / T.parts[T.i].t, 0, 1);
    S.transPhase = part;
    FH.hudAlpha = part === 'tally' ? 1 : Math.max(0, 1 - (part === 'flash' ? u : 1));
    if (part === 'flash') { S.flash(0.3 * (1 - u)); S.cockRise = 1 - u; return; }
    S.flash(0);
    if (part === 'tally') { S.cockRise = 0; rigPose(w, 0, ctx, false); return; }
    if (part === 'fade') { S.canvasFade(1 - u); return; }
    S.eyeIn = false;
    // the rise: distance in log space (video-x6:17), the lens back to the 2D frame's, over the 2D centre at its zoom
    S.cockRise = 0;
    var s = trapezoid(u), sm = smooth(u), fr = T.from, to = T.to;
    var D = logLerp(fr.D, to.D, s), fov = lerp(fr.fov, to.fov, sm);
    var Tt = V.b.copy(T.ship0).lerp(to.T, sm);
    lens(cam, fov, D, { x: to.off.x * sm, y: to.off.y * sm });
    orbitPose(cam, Tt, D, lerpAngle(fr.yaw, -Math.PI / 2, sm), lerp(fr.down, Math.PI / 2, sm), 0);
    S.exitSamples.push([r3(u), r1(D)]);
  }
  function finishExit(ctx) {
    tlCloseAll();
    S.tl = null; S.transPhase = null; S.flash(0);
    FH.tally = null;
    var cs = ctx.ship; cs.alt = null; cs.pitch = 0; cs.rollDeg = null;   // the altitude springs back to the 2D plane
    if (S.landUid) C.landOn = S.landUid;
    ctx.transition.done(null);
  }

  // ---------------------------------------------------------------- "Fly to" (on top of __3dApproach): a log-space hop
  function flyTo(uid) {
    if (!S || !S.world || S.tl || ctx0().mode !== 'fp') return false;
    var w = S.world, p = (w.planets || []).filter(function (x) { return x.uid === uid; })[0];
    if (!p) return false;
    var sp = w.ship || {}, d = Math.hypot(p.x - (Number(sp.x) || 0), p.y - (Number(sp.y) || 0));
    S.tl = { kind: 'flyto', el: 0, uid: uid, d: d, warp: d > FLYTO_WARP, placed: false, from: shipPos(w, new S.THREE.Vector3()), peak: Math.max(300, d * 0.6) };
    S.tl.cur = tlOpen('flyto');
    if (S.tl.warp) S.tl.warpE = tlOpen('warp');
    return true;
  }
  function ctx0() { return S.ctx; }
  function runFlyTo(w, dt, ctx) {
    var T = S.tl, cam = ctx.camera, u = clamp((T.el += dt) / FLYTO_T, 0, 1);
    if (!T.placed && u >= 0.5) {
      T.placed = true;
      if (typeof g.__3dApproach === 'function') g.__3dApproach(T.uid);
      S.rig = 'cockpit';
    }
    var ship = shipPos(w, V.a.clone()), hdg = Number((w.ship || {}).hdg) || 0;
    S.eyeIn = false;
    var dd = u < 0.5 ? logLerp(2, T.peak, trapezoid(u / 0.5)) : logLerp(T.peak, 2, trapezoid((u - 0.5) / 0.5));
    var Tt = u < 0.5 ? V.b.copy(T.from) : ship;
    lens(cam, S.feel.fov, dd, null);
    orbitPose(cam, Tt, dd, hdg, Math.min(60, dd / 20) * D2R, 0);
    S.minClear = Math.min(S.minClear, clearOf(w, cam));
    warpUpdate(dt, T.warp && u < 0.9);
    S.flyDist = S.flyDist || []; S.flyDist.push(r1(dd));
    if (u >= 1) {
      tlClose('flyto'); if (T.warpE) tlClose('warp');
      S.tl = null; rigPose(w, 0, ctx, true);
    }
  }

  // ---------------------------------------------------------------- input: keys, the touch deck
  function publishFlightInput() {
    if (S && S.ctx.page && typeof S.ctx.page.flightInput === 'function')
      S.ctx.page.flightInput({ pitch: (S.input.up ? 1 : 0) - (S.input.down ? 1 : 0), level: !!S.input.level });
  }
  function applyView(view) {
    endEvent(); S.rig = view; S.nsQ = null; S.dist = null; S.camYaw = null;
    rigPose(S.world, 0, S.ctx, true);
  }
  function view3d(view) {
    if (!S) return null;
    if (view == null) return (S.ev ? S.ev.prev : S.rig) === 'chase' ? 'chase' : 'cockpit';
    if ((view !== 'cockpit' && view !== 'chase') || S.ctx.transition.phase() === 'exit') return false;
    applyView(view); S.set.view = view;
    if (S.ctx.page.settings) S.ctx.page.settings.set('view3d', view);
    if (S.tl && S.tl.kind === 'enter') finishEnter(S.ctx, true);
    return view;
  }
  function onKey(e) {
    if (!S) return false;
    var k = String(e.key || '').toLowerCase();
    S.lastInput = S.t;
    if (S.guide && S.guide.firstOpen && k !== 'tab' && k !== 'shift' && k !== 'control' && k !== 'alt' && k !== 'meta') { S.guide.firstOpen = false; S.guide.help.open = false; }   // flying closes the first-entry list
    // any key skips the entry or exit timeline (camCut)
    if (S.tl && (S.tl.kind === 'enter' || S.tl.kind === 'exit')) {
      if (e.repeat) return true;
      if (S.tl.kind === 'enter') finishEnter(S.ctx, true);
      else { tlCloseAll(); finishExit(S.ctx); }
      if (S && S.trAudit) S.trAudit.phase = null;            // the audit says so before the next frame
      return true;
    }
    if (S.rig === 'event') { endEvent(); rigPose(S.world, 0, S.ctx, true); if (k === 'v') return true; }
    if (k === 'r' || k === 'f' || k === 'x') {
      if (S.ctx.mode !== 'paused') {
        if (k !== 'x') { clearTimeout(S.levelTimer); S.input.level = false; }
        S.input[k === 'r' ? 'up' : k === 'f' ? 'down' : 'level'] = true; publishFlightInput();
      }
      return true;
    }
    if (k === 'v') { if (!e.repeat) view3d(view3d() === 'cockpit' ? 'chase' : 'cockpit'); return true; }
    if (k === 'enter') {
      // Enter on a locked planet: land in its 2D focus (the exit ends over it; the page then opens it)
      var L = FH.lock;
      if (!e.repeat && L && L.state === 'locked' && L.uid && L.kind === 'planet') { S.landUid = L.uid; C.leave('key'); }
      return true;
    }
    if (typeof FH.hudKey === 'function') { var took = FH.hudKey(e); if (took) return true; }
    return false;
  }
  function onKeyUp(e) {
    if (!S) return;
    var k = String(e.key || '').toLowerCase();
    if (k === 'r') S.input.up = false;
    if (k === 'f') S.input.down = false;
    if (k === 'x') S.input.level = false;
    publishFlightInput();
    if (typeof FH.hudKeyUp === 'function') FH.hudKeyUp(e);
  }
  function anyInput() { if (S) { S.lastInput = S.t; if (S.guide && S.guide.hint) S.guide.hint = null; if (S.guide && S.guide.firstOpen) { S.guide.firstOpen = false; S.guide.help.open = false; } } }
  function flightGuideBuild() {
    var d = g.document, root = d.createElement('aside');
    root.id = 'flight3d-instruments'; root.className = 'flight3d-instruments'; root.setAttribute('aria-label', 'Flight instruments');
    var stats = d.createElement('div'); stats.className = 'flight3d-stats'; root.appendChild(stats);
    var help = d.createElement('details'), title = d.createElement('summary'); title.textContent = 'Controls'; help.appendChild(title);
    var text = d.createElement('div'); text.className = 'flight3d-controls';
    var pg = S.ctx.page || {}, dives = !(pg.settings && pg.settings.get('pitchKeys', 'dive') === 'climb');
    text.textContent = S.ctx.quality.phone ? 'The ship flies by itself. Drag the STEER circle (bottom left) to turn: ' + (dives ? 'up points the nose down' : 'up points the nose up')
      + ' (Settings swaps it). BOOST goes faster, BRAKE slower, SELECT picks the planet ahead.'
      : 'The ship always flies forward\nA / D or ← / →  turn\n' + (dives ? 'W / ↑  nose down · S / ↓  nose up' : 'W / ↑  nose up · S / ↓  nose down')
      + '\nQ / E  tilt the wings (double-tap: roll)\nShift  boost · C  brake\nL  select the planet ahead · Tab  select the next\nEnter  open the selected planet\nV  change camera · Esc  pause · I  map';
    if (S.ctx.quality.phone) {
      var level = d.createElement('button'); level.type = 'button'; level.className = 'flight3d-level'; level.textContent = 'Level nose';
      level.addEventListener('click', function () {
        var owner = S; if (!owner || owner.ctx.mode === 'paused') return;
        clearTimeout(owner.levelTimer); owner.input.level = true; publishFlightInput(); help.open = false;
        owner.levelTimer = setTimeout(function () { if (S === owner) { S.input.level = false; publishFlightInput(); } }, 1500);
      });
      text.appendChild(level);
    }
    help.appendChild(text); root.appendChild(help); d.body.appendChild(root);
    S.guide = { root: root, stats: stats, help: help, at: -1, firstOpen: false };
    // the keys list opens by itself on a newcomer's first entry (remembered in localStorage; a failing store opens it once
    // per session at most) and stays one click away under "Controls" after that
    var seen = false, store = null;
    try { store = g.localStorage; seen = !!store && store.getItem('cm3d.controlsSeen') === '1'; } catch (e) { seen = false; store = null; }
    if (!seen && !g.__cm3dControlsShown) {
      g.__cm3dControlsShown = true;
      try { if (store) store.setItem('cm3d.controlsSeen', '1'); } catch (e) { /* private mode: shown once per session */ }
      // a phone never opens the card over the screen: one line in the readout strip says how to steer instead
      if (S.ctx.quality.phone) S.guide.hint = 'Drag the STEER circle to turn';
      else { help.open = true; S.guide.firstOpen = true; }
    }
  }
  function flightGuideUpdate(ctx) {
    var guide = S.guide, state = ctx.page && ctx.page.flightState ? ctx.page.flightState() : null;
    if (!guide) return;
    guide.root.hidden = !(state && ctx.mode === 'fp' && !S.tl && !ctx.hudHidden);
    if (guide.root.hidden || S.t - guide.at < 0.1) return;
    guide.at = S.t;
    // the first-entry keys list steps aside by itself after a few seconds of flight, so on a phone it never hides the
    // HUD for long; "Controls" opens it again
    if (guide.firstOpen) {
      if (guide.openAt == null) guide.openAt = S.t;
      else if (S.t - guide.openAt > 8) { guide.firstOpen = false; guide.help.open = false; }
    }
    // small and secondary: the speed, then height and climb only when they are not level, in words
    if (guide.hint && S.t - (guide.hintAt == null ? (guide.hintAt = S.t) : guide.hintAt) > 8) guide.hint = null;
    var alt = Math.round(state.alt), climb = Math.round(state.climb), parts = [guide.hint || 'Speed: ' + (state.speed > 270 ? 'boosting' : state.speed < 150 ? 'slowing down' : 'cruising')];
    if (!guide.hint) {
      if (Math.abs(alt) >= 5) parts.push(Math.abs(alt) + (alt > 0 ? ' above' : ' below') + ' the map');
      if (Math.abs(climb) >= 5) parts.push(climb > 0 ? 'climbing' : 'diving');
      if (state.edge) parts.push(state.edge === 'y' ? 'at the height limit' : 'at the map edge');
    }
    guide.stats.textContent = parts.join('  ·  ');
  }
  // synthetic key events on the window: the page's own flight reads them as it reads the keyboard
  function press(key, down) {
    try { g.dispatchEvent(new g.KeyboardEvent(down ? 'keydown' : 'keyup', { key: key, bubbles: true })); } catch (e) { /* no window */ }
  }
  var DECK_KEYS = { left: 'a', right: 'd', up: 'w', down: 's', boost: 'Shift', brake: 'c' };   // the keyboard's own keys
  function held(name, on) {
    var I = S.input;
    if (!!I.held[name] === !!on) return;
    I.held[name] = !!on;
    if (on && (name === 'up' || name === 'down')) { clearTimeout(S.levelTimer); I.level = false; }
    if (DECK_KEYS[name]) press(DECK_KEYS[name], on);
    publishFlightInput();
  }
  function releaseAll() {
    if (!S) return;
    Object.keys(S.input.held).forEach(function (n) { held(n, false); });
    S.input.up = S.input.down = S.input.level = false;
    publishFlightInput();
    S.deck.gen++;
    if (S.deck.rest) S.deck.rest();
  }
  // the deck for touch input: a left-half stick, BOOST and BRAKE
  // holds of at least 80 ms (a generation counter, so a fast re-press is never cut by the last one's timer), empty taps
  // pass through to lock what they touch, two fingers pause, everything lets go on blur, a hidden page, the pause card and
  // Back, and the deck hides while a gamepad is plugged in
  function deckBuild(ctx) {
    var d = g.document, D = S.deck = { gen: 0, root: null, pointers: {}, stickId: null, taps: {}, visible: false };
    if (!ctx.quality.phone && !(g.matchMedia && g.matchMedia('(pointer:coarse)').matches)) return;
    var root = d.getElementById('deck3d');
    if (!root) { root = d.createElement('div'); root.id = 'deck3d'; root.setAttribute('aria-label', 'Flight controls'); d.body.appendChild(root); }
    root.innerHTML = '';
    root.className = 'deck3d';
    var stickZone = d.createElement('div'); stickZone.className = 'deck3d-stick-zone'; root.appendChild(stickZone);
    var base = d.createElement('div'); base.className = 'deck3d-base'; root.appendChild(base);
    var knob = d.createElement('div'); knob.className = 'deck3d-knob'; root.appendChild(knob);
    var steer = d.createElement('div'); steer.className = 'deck3d-steer'; steer.textContent = 'STEER'; root.appendChild(steer);
    var mk = function (name, label) {
      var b = d.createElement('button'); b.type = 'button'; b.className = 'deck3d-btn deck3d-' + name; b.textContent = label; b.setAttribute('aria-label', name === 'lock' ? 'Select the planet ahead' : label);
      root.appendChild(b); return b;
    };
    var boost = mk('boost', 'BOOST'), brake = mk('brake', 'BRAKE'), lock = mk('lock', 'SELECT PLANET'), map = mk('map', 'MAP');
    D.root = root; D.stick = knob; D.base = base; D.steer = steer; D.zone = stickZone; D.boost = boost; D.brake = brake;
    // the stick rests at (22%, 86%) of the screen (comps/3d-phone.png); a touch anywhere in the zone moves it there
    D.rest = function () {
      var sz = viewSize(), cx = Math.round(sz.w * 0.22), cy = Math.round(sz.h - Math.max(86, sz.h * 0.137));
      D.cx = cx; D.cy = cy;
      [base, knob].forEach(function (e) { e.style.left = cx + 'px'; e.style.top = cy + 'px'; });
      steer.style.left = cx + 'px'; steer.style.top = (cy + 64) + 'px';
    };
    D.rest();
    var hold = function (btn, name) {
      btn.addEventListener('pointerdown', function (e) {
        e.preventDefault(); e.stopPropagation();
        try { btn.setPointerCapture(e.pointerId); } catch (x) { /* synthetic */ }
        var gen = ++D.gen; D.taps[name] = { at: nowMs(), gen: gen };
        held(name, true); btn.classList.add('on'); anyInput();
      });
      var up = function (e) {
        if (e) { e.preventDefault(); e.stopPropagation(); }
        var tp = D.taps[name]; if (!tp) return;
        var left = Math.max(0, 80 - (nowMs() - tp.at)), gen = tp.gen;
        setTimeout(function () {
          if (!S || D.gen !== gen) return;                // a newer press owns the key now
          held(name, false); btn.classList.remove('on');
          if (name === 'boost') S.input.boostHeldMs = Math.round(nowMs() - tp.at);
          D.taps[name] = null;
        }, left);
      };
      btn.addEventListener('pointerup', up); btn.addEventListener('pointercancel', up);
    };
    hold(boost, 'boost'); hold(brake, 'brake');
    lock.addEventListener('click', function (e) { e.preventDefault(); anyInput(); if (typeof FH.tapLock === 'function') FH.tapLock(-1, -1); });
    map.addEventListener('click', function (e) { e.preventDefault(); releaseAll(); C.leave('back'); });
    stickZone.addEventListener('pointerdown', function (e) {
      e.preventDefault(); anyInput();
      try { stickZone.setPointerCapture(e.pointerId); } catch (x) { /* synthetic */ }
      D.stickId = e.pointerId; D.sx = e.clientX; D.sy = e.clientY;
      [base, knob].forEach(function (el) { el.style.left = e.clientX + 'px'; el.style.top = e.clientY + 'px'; });
    });
    stickZone.addEventListener('pointermove', function (e) {
      if (D.stickId !== e.pointerId) return;
      var r = 48, dx = clamp((e.clientX - D.sx) / r, -1, 1), dy = clamp((e.clientY - D.sy) / r, -1, 1);
      knob.style.left = (D.sx + dx * r) + 'px'; knob.style.top = (D.sy + dy * r) + 'px';
      held('left', dx < -0.3); held('right', dx > 0.3); held('up', dy < -0.35); held('down', dy > 0.35);
    });
    var stickUp = function (e) { if (D.stickId !== e.pointerId) return; D.stickId = null; D.rest(); ['left', 'right', 'up', 'down'].forEach(function (n) { held(n, false); }); };
    stickZone.addEventListener('pointerup', stickUp); stickZone.addEventListener('pointercancel', stickUp);
    // empty taps on the 3D canvas: one finger locks what it touches, two fingers pause
    var cv = ctx.renderer && ctx.renderer.domElement;
    if (cv) {
      D.cv = cv;
      D.onDown = function (e) { D.pointers[e.pointerId] = { x: e.clientX, y: e.clientY, at: nowMs() }; anyInput(); };
      D.onUp = function (e) {
        var p = D.pointers[e.pointerId], n = Object.keys(D.pointers).length; delete D.pointers[e.pointerId];
        if (!p) return;
        if (n >= 2) { if (!S.pausedByDeck) { S.pausedByDeck = true; releaseAll(); C.togglePause(); setTimeout(function () { if (S) S.pausedByDeck = false; }, 300); } return; }
        if (nowMs() - p.at < 350 && Math.hypot(e.clientX - p.x, e.clientY - p.y) < 14 && typeof FH.tapLock === 'function') FH.tapLock(e.clientX, e.clientY);
      };
      cv.addEventListener('pointerdown', D.onDown); cv.addEventListener('pointerup', D.onUp); cv.addEventListener('pointercancel', D.onUp);
    }
    D.visible = true;
  }
  function deckUpdate(ctx) {
    var D = S.deck; if (!D || !D.root) return;
    var pads = false;
    try { pads = !!(g.navigator.getGamepads && Array.prototype.some.call(g.navigator.getGamepads() || [], function (p) { return !!p; })); } catch (e) { pads = false; }
    var show = !pads && ctx.mode === 'fp' && !S.tl && !ctx.hudHidden;
    if (!show && D.visible) releaseAll();
    D.visible = show; S.input.gamepad = pads;
    D.root.style.display = show ? 'block' : 'none';
    if (show && D.stickId == null) D.rest();
  }
  function deckDispose() {
    var D = S && S.deck; if (!D) return;
    releaseAll();
    if (D.cv) { D.cv.removeEventListener('pointerdown', D.onDown); D.cv.removeEventListener('pointerup', D.onUp); D.cv.removeEventListener('pointercancel', D.onUp); }
    if (D.root) { D.root.style.display = 'none'; D.root.innerHTML = ''; }
  }
  function nowMs() { return g.performance && g.performance.now ? g.performance.now() : Date.now(); }

  // ---------------------------------------------------------------- pause: the framed 2D map, CONTINUE / RETURN TO MAP
  // (tcrf §1.8.10: the sequel's pause is the map itself). The core makes #pause3d and owns its two buttons' actions; this
  // dresses it: the live 2D map (the world's planets, the sun, agents, problems and the ship, north up, drawn from the
  // same data) framed in the middle over the dimmed scene, the buttons' plain capitals, and a "meanwhile" line
  function pauseDress(w, ctx) {
    var d = g.document, el = d.getElementById('pause3d');
    if (!el) return;
    if (!el.__b12) {
      el.__b12 = true;
      var btns = el.querySelectorAll('button');
      if (btns[0]) btns[0].textContent = 'CONTINUE';
      if (btns[1]) btns[1].textContent = 'RETURN TO MAP';
      var head = el.querySelector('b'); if (head) head.textContent = 'PAUSED';
      var hint = el.querySelector('div'); if (hint) { hint.className = 'pause3d-hint'; }
      var frame = d.createElement('div'); frame.className = 'pause3d-frame';
      var cv = d.createElement('canvas'); cv.className = 'pause3d-map'; cv.setAttribute('aria-label', 'The map, live');
      var mean = d.createElement('div'); mean.className = 'pause3d-meanwhile'; mean.setAttribute('role', 'status');
      frame.appendChild(cv);
      el.insertBefore(frame, btns[0] || null);
      el.insertBefore(mean, btns[0] || null);
      el.classList.add('pause3d-b12');
    }
    var cvs = el.querySelector('canvas.pause3d-map'), mw = el.querySelector('.pause3d-meanwhile');
    if (cvs) drawMap(cvs, w, Math.min(720, Math.round(viewSize().w * 0.56)), Math.min(400, Math.round(viewSize().h * 0.46)));
    if (mw) mw.textContent = meanwhile(w);
  }
  function meanwhile(w) {
    var f = FH.follow, parts = [];
    if (f) parts.push(f.title + ' is at step ' + Math.max(1, f.index + 1) + ' of ' + f.total + (f.place ? ' in ' + f.place : ''));
    var c = w.counts || {}, ny = typeof c.need_you === 'number' ? c.need_you : 0;
    var working = (w.craft || []).filter(function (x) { return x.role !== 'carrier' && String(x.state).indexOf('work') >= 0; }).length;
    parts.push(ny + ' need' + (ny === 1 ? 's' : '') + ' you');
    parts.push(working + ' agent' + (working === 1 ? '' : 's') + ' working');
    return 'Meanwhile: ' + parts.join(' · ') + '. The world keeps running while you are paused.';
  }
  var PURPOSE_HEX = { code: '#4e79c8', notes: '#3aa0b8', reference: '#c8a060', data: '#d08a3c', media: '#3fd6a0', config: '#7ec8f0', shared: '#8fa8c0', archive: '#8a6a40', unsorted: '#8a93a6' };
  // the 2D map from the world: north up, everything framed; planets in their purpose colour lit from the sun side
  function drawMap(cv, w, cw, chh) {
    var dpr = Math.min(2, g.devicePixelRatio || 1);
    if (cv.width !== Math.round(cw * dpr)) { cv.width = Math.round(cw * dpr); cv.height = Math.round(chh * dpr); cv.style.width = cw + 'px'; cv.style.height = chh + 'px'; }
    var x = cv.getContext('2d'); x.setTransform(dpr, 0, 0, dpr, 0, 0);
    x.fillStyle = '#05070D'; x.fillRect(0, 0, cw, chh);
    var bodies = (w.planets || []).concat(w.star ? [w.star] : []), sp = w.ship || {};
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    bodies.concat([{ x: sp.x, y: sp.y, r: 40 }]).forEach(function (b) { minX = Math.min(minX, b.x - b.r); maxX = Math.max(maxX, b.x + b.r); minY = Math.min(minY, b.y - b.r); maxY = Math.max(maxY, b.y + b.r); });
    var pad = 24, k = Math.min((cw - 2 * pad) / Math.max(1, maxX - minX), (chh - 2 * pad) / Math.max(1, maxY - minY));
    var ox = (cw - (maxX - minX) * k) / 2 - minX * k, oy = (chh - (maxY - minY) * k) / 2 - minY * k, P = function (vx, vy) { return [ox + vx * k, oy + vy * k]; };
    // faint grid
    x.strokeStyle = 'rgba(34,48,74,0.55)'; x.lineWidth = 1;
    for (var gx = 0; gx < cw; gx += 40) { x.beginPath(); x.moveTo(gx + 0.5, 0); x.lineTo(gx + 0.5, chh); x.stroke(); }
    for (var gy = 0; gy < chh; gy += 40) { x.beginPath(); x.moveTo(0, gy + 0.5); x.lineTo(cw, gy + 0.5); x.stroke(); }
    var st = w.star;
    if (st) { var s0 = P(st.x, st.y), sg = x.createRadialGradient(s0[0], s0[1], 0, s0[0], s0[1], Math.max(6, st.r * k * 2));
      sg.addColorStop(0, '#FFF2C8'); sg.addColorStop(0.35, '#F5B342'); sg.addColorStop(1, 'rgba(245,179,66,0)'); x.fillStyle = sg; x.beginPath(); x.arc(s0[0], s0[1], Math.max(6, st.r * k * 2), 0, TAU); x.fill(); }
    var f = FH.follow;
    (w.planets || []).forEach(function (p) {
      var c = P(p.x, p.y), r = Math.max(4, p.r * k), col = PURPOSE_HEX[p.purpose] || PURPOSE_HEX.unsorted;
      var lx = st ? st.x - p.x : -1, ly = st ? st.y - p.y : -1, ln = Math.hypot(lx, ly) || 1;
      var gr = x.createRadialGradient(c[0] + lx / ln * r * 0.45, c[1] + ly / ln * r * 0.45, r * 0.1, c[0], c[1], r);
      gr.addColorStop(0, '#FFFFFF'); gr.addColorStop(0.18, col); gr.addColorStop(1, '#0B1220');
      x.fillStyle = gr; x.beginPath(); x.arc(c[0], c[1], r, 0, TAU); x.fill();
      var cnt = p.counts || {};
      if (cnt.problems) { x.strokeStyle = '#FF5A5F'; x.lineWidth = 2; x.beginPath(); x.arc(c[0], c[1], r + 3, 0, TAU); x.stroke(); }
      else if (cnt.need_you) { x.strokeStyle = '#FFB547'; x.lineWidth = 2; x.beginPath(); x.arc(c[0], c[1], r + 3, 0, TAU); x.stroke(); }
      if (f && f.planet === p.uid) { x.strokeStyle = '#6FB7FF'; x.lineWidth = 2; x.setLineDash([4, 3]); x.beginPath(); x.arc(c[0], c[1], r + 7, 0, TAU); x.stroke(); x.setLineDash([]); }
      x.font = '600 13px "Barlow Condensed","Arial Narrow",sans-serif'; x.textAlign = 'center'; x.textBaseline = 'top';
      x.fillStyle = '#E8EEF6'; x.fillText(String(p.label), c[0], c[1] + r + 4);
    });
    (w.craft || []).forEach(function (cr) { if (cr.role === 'carrier') return; var c = P(cr.x, cr.y); x.fillStyle = cr.colour || '#CFE3FF'; x.beginPath(); x.moveTo(c[0], c[1] - 5); x.lineTo(c[0] + 4, c[1] + 4); x.lineTo(c[0] - 4, c[1] + 4); x.closePath(); x.fill(); });
    var h = Number(sp.hdg) || 0, s1 = P(sp.x, sp.y);
    x.save(); x.translate(s1[0], s1[1]); x.rotate(h + Math.PI / 2);
    x.fillStyle = '#4DE8E0'; x.beginPath(); x.moveTo(0, -9); x.lineTo(6, 7); x.lineTo(0, 4); x.lineTo(-6, 7); x.closePath(); x.fill(); x.restore();
    x.font = '600 12px "Barlow Condensed","Arial Narrow",sans-serif'; x.textAlign = 'left'; x.fillStyle = '#9AA8BC'; x.fillText('you', s1[0] + 9, s1[1] - 6);
  }

  // ---------------------------------------------------------------- overlays: the flash, the reduced-motion crossfade
  function overlayEl(id, cls) {
    var d = g.document, el = d.getElementById(id);
    if (!el) { el = d.createElement('div'); el.id = id; d.body.appendChild(el); }
    el.className = cls; return el;
  }

  // ---------------------------------------------------------------- the spill and the events this module answers
  function spillFor(kind) {
    var sp = S.spill;
    if (S.world && S.world.reduced) return;
    if (S.set.reduceFlashing) return;
    if (sp.lastAt != null && S.t - sp.lastAt < SPILL_GAP) { sp.suppressed++; return; }
    sp.lastAt = S.t; sp.on = true; sp.age = 0; sp.kind = kind; sp.colour = EVENT_COL[kind] || '#FFFFFF'; sp.count++;
  }
  function eventsUpdate(ctx) {
    var list = FH.events || [];
    list.forEach(function (ev) {
      if (ev.seq <= S.seenSeq) return;
      S.seenSeq = ev.seq;
      if (!FH.follow || ev.wf !== FH.follow.id) return;
      if (ev.kind === 'done') { spillFor('done'); S.stepsDone++; if (ctx.quality.phone) ctx.page.haptic('done'); }
      else if (ev.kind === 'failed') { spillFor('failed'); if (ctx.quality.phone) ctx.page.haptic('fail'); }
      else if (ev.kind === 'needs') spillFor('needs');
      else if (ev.kind === 'complete') {
        if (ctx.quality.phone) ctx.page.haptic('done');
        S.pendingEvent = { uid: ev.planet, at: S.t };
      }
    });
    // the event camera waits for 3 s with no input, then shows the finished planet
    var pe = S.pendingEvent;
    if (pe && ctx.mode === 'fp' && !S.tl && S.t - S.lastInput >= IDLE_FOR && pe.uid) { S.pendingEvent = null; startEvent(pe.uid); }
    if (pe && S.t - pe.at > 30) S.pendingEvent = null;
  }

  // ---------------------------------------------------------------- the module
  C.module('camera', {
    build: function (scene, world, ctx) {
      var THREE = ctx.THREE;
      V = { a: new THREE.Vector3(), b: new THREE.Vector3(), c: new THREE.Vector3(), d: new THREE.Vector3(), e: new THREE.Euler(0, 0, 0, 'YXZ'), q: new THREE.Quaternion() };
      entries++;
      FH.events = []; FH.seq = 0; FH.follow = null; FH.hudAlpha = 0; FH.caption = null; FH.targetCard = null; FH.tally = null; FH.lock = null;
      S = {
        THREE: THREE, ctx: ctx, world: world, t: 0, feel: resolveFeel(ctx.skin3d), rev: null, scanned: false, prevWf: {}, prevFollowId: null,
        prevFoes: {}, prevCraft: {}, timeline: [{ name: 'match', from: 0, to: null }], tl: null, transPhase: 'match', match: null, camOff: null,
        rig: 'cockpit', dist: null, camYaw: null, swing: 0, camRoll: 0, rollRate: 0, viewRoll: 0, fovKick: 0, minClear: Infinity,
        att: { pitch: 0, cosAlt: 0, loopLift: 0, lead: 0, lastHdg: null, rollDeg: 0, alt: 0, lift: null },
        input: { up: false, down: false, held: {}, boostHeldMs: 0, gamepad: false }, spill: { on: false, age: 0, lastAt: null, suppressed: 0, count: 0, kind: null },
        lastInput: 0, seenSeq: 0, finished: 0, stepsDone: 0, skips: 0, evCount: 0, cardUid: null, entered: false, cockRise: 0,
        maxR: (world.planets || []).reduce(function (m, p) { return Math.max(m, p.r || 0); }, 0), orbit: null, landUid: null,
        glareK: 0, coronaK: 0, planned: null
      };
      ctx.glareK = 0; ctx.coronaK = 0;                 // the space lane's sun glow and flare, scaled by the entry (ctx.glareK, ctx.coronaK)
      FH.feel = S.feel;
      S.set = settings(ctx);
      S.rig = S.set.view;
      // ctx.rig is the camera's rig for every module (the core writes its own into it each frame; the setter keeps that
      // as S.coreRig, so __3dApproach's cut to the cockpit still reaches this module): 'cockpit' while the eye is in the
      // ship (the cockpit and the no-ship view: no module draws the own ship then), 'chase' while the camera is outside
      // it (the entry's dive and orbit, the exit's rise, a hop), else the rig
      S.coreRig = ctx.rig; S.ctxObj = ctx;
      Object.defineProperty(ctx, 'rig', { configurable: true, enumerable: true,
        get: function () { return !S ? 'chase' : S.eyeIn ? 'cockpit' : (S.rig === 'cockpit' || S.rig === 'noship' ? 'chase' : S.rig); },
        set: function (v) { if (S) S.coreRig = v; } });
      // an entry from a planet's card names it (the core locks it before the first frame): the target card shows it
      S.cardUid = undefined;
      S.region = region(world);
      cockpitBuild(ctx);
      var onAfter = scene.onAfterRender, K = S.cock;
      K.prevAfter = onAfter;
      scene.onAfterRender = function (renderer, sc, camera) {
        if (typeof onAfter === 'function') onAfter.apply(this, arguments);
        if (S && camera === ctx.camera && !K.inPass) { K.inPass = true; try { cockpitRender(renderer); } finally { K.inPass = false; } }
      };
      S.scene = scene;
      var flash = overlayEl('flash3d', 'flash3d');
      S.flash = function (a) { var on = a > 0.001; if (on && !S.flashOn) S.flashes++; S.flashOn = on; flash.style.opacity = String(clamp(a, 0, 0.3)); flash.style.display = on ? 'block' : 'none'; };
      S.flashes = 0;
      var canvas = ctx.renderer && ctx.renderer.domElement;
      S.canvasFade = function (a) { if (canvas) canvas.style.opacity = a >= 1 ? '' : String(clamp(a, 0, 1)); };
      if (world.reduced) S.canvasFade(0);
      S.onKeyUp = onKeyUp; S.onBlur = function () { releaseAll(); }; S.onVis = function () { if (g.document.hidden) releaseAll(); };
      S.onPointer = anyInput;
      g.addEventListener('keyup', S.onKeyUp); g.addEventListener('blur', S.onBlur); g.document.addEventListener('visibilitychange', S.onVis);
      g.addEventListener('pointerdown', S.onPointer, true);
      deckBuild(ctx);
      flightGuideBuild();
      C.onKey = onKey;
      C.view3d = view3d;
      C.flyTo = flyTo;
      FH.flyTo = flyTo;
      scan(world);
    },
    rebuild: function (scene, world, ctx) {
      if (!S) return;
      S.world = world;
      S.maxR = (world.planets || []).reduce(function (m, p) { return Math.max(m, p.r || 0); }, 0);
      scan(world);
    },
    update: function (world, sdt, ctx) {
      if (!S) return;
      var dt = ctx.dt || 0, a = ctx.audit;
      S.t = ctx.t; S.world = world; S.ctx = ctx;
      var nextSet = settings(ctx);
      if (nextSet.view !== S.set.view) applyView(nextSet.view);
      S.set = nextSet;
      if (S.cardUid === undefined) S.cardUid = ctx.lock || null;
      scan(world);
      var tp = ctx.transition.phase();
      if (tp === 'demo') return;
      // a rig change from the core (__3dApproach cuts to the cockpit)
      if (S.coreRig && S.coreRig !== S.lastCoreRig) { if (S.lastCoreRig != null && S.coreRig === 'cockpit' && S.rig !== 'event') S.rig = 'cockpit'; S.lastCoreRig = S.coreRig; }
      if (S.timeline[0].to == null && tp !== 'match') S.timeline[0].to = r3(S.t);
      if (tp === 'match') {
        S.transPhase = 'match'; FH.hudAlpha = 0; S.glareK = world.reduced ? 1 : 0; if (world.reduced) S.canvasFade(clamp(S.t / REDUCED_T, 0, 1));
        if (!S.layer && !S.layerTried && !world.reduced) { S.layerTried = true; layerBuild(ctx); }
        layerSet(1, 0);
      }
      if (ctx.mode === 'paused') releaseAll();
      attitude(world, tp === 'fp' && ctx.mode !== 'paused' ? dt : 0, ctx);
      // Boost widens the lens; release settles it more slowly, with reduced motion keeping the lens still.
      var boosting = !!(world.ship && world.ship.boost) && !world.reduced;
      var lensTarget = boosting ? num(S.feel.fovBoost, 0) : 0;
      var lensRemaining = Math.exp(-Math.max(0, dt) * (boosting ? 12 : 5));
      S.fovKick = lensTarget + (S.fovKick - lensTarget) * lensRemaining;
      if (tp === 'enter') {
        if (!S.tl || S.tl.kind !== 'enter') startEnter(world, ctx);
        runEnter(world, dt, ctx);
      } else if (tp === 'exit') {
        if (!S.tl || S.tl.kind !== 'exit') { if (S.tl) tlCloseAll(); startExit(world, ctx); }
        runExit(world, dt, ctx);
      } else if (tp === 'fp') {
        if (S.layer) layerDispose();                   // an entry the core ended at its first frame (__3dApproach) too
        if (S.tl && S.tl.kind === 'enter') {          // the core ended the entry itself (__3dApproach, its own timeout)
          tlCloseAll(); layerDispose(); S.tl = null; FH.hudAlpha = 1; FH.caption = null; FH.targetCard = null; FH.entry = null; S.cockRise = 1; S.canvasFade(1); S.entered = true;
        }
        if (S.tl && S.tl.kind === 'flyto') runFlyTo(world, dt, ctx);
        else { if (!S.entered) { S.entered = true; FH.hudAlpha = 1; S.cockRise = 1; S.canvasFade(1); } rigPose(world, dt, ctx, false); warpUpdate(dt, boosting); }
        S.transPhase = S.tl ? S.tl.kind : null;
        FH.hudAlpha = Math.min(1, (FH.hudAlpha || 0) + dt * 3);
        S.glareK = Math.min(1, S.glareK + dt / 0.4);    // an entry skipped by a key: the glow comes up over 0.4 s, never pops
        S.coronaK = 1;
      }
      if (S) { ctx.glareK = S.glareK; ctx.coronaK = Math.max(S.coronaK || 0, S.glareK); }
      if (!S) return;                                  // the exit's last frame ended the view (dispose ran)
      eventsUpdate(ctx);
      S.region = region(world);
      var showCock = (S.rig === 'cockpit') && tp !== 'match' && !(S.tl && S.tl.kind === 'flyto' && S.tl.el < FLYTO_T * 0.9) && S.cockRise > 0.001;
      if (tp === 'exit' && S.transPhase !== 'flash') showCock = false;
      cockpitUpdate(ctx, dt, showCock, S.cockRise);
      ctx.scene.children.forEach(function (o) { if (o.userData && o.userData.uid === 'ship' && o.userData.kind === 'ship') o.visible = !S.eyeIn; });
      if (ctx.mode === 'paused') pauseDress(world, ctx);
      deckUpdate(ctx);
      flightGuideUpdate(ctx);
      // the audit: camera.*, transition.*, input.*
      var cam = ctx.camera, K = S.cock, sz = viewSize();
      var gp = K.glassNdc ? [Math.round((K.glassNdc[0] + 1) / 2 * sz.w), Math.round((1 - K.glassNdc[1]) / 2 * sz.h)] : null;
      var maxOrder = K.meshes.reduce(function (m, x) { return Math.max(m, x.renderOrder); }, 0);
      a.camera = {
        rig: S.rig, fov: r1(cam.fov), pos: [r1(cam.position.x), r1(cam.position.y), r1(cam.position.z)],
        yaw: r1(((Number((world.ship || {}).hdg) || 0) * R2D + 360) % 360), pitch: r1(S.att.pitch), roll: r1(((S.camRoll + 540) % 360) - 180),
        dist: r1(S.dist || 0), swing: r1(S.swing || 0), rollRate: r1(S.rollRate || 0),
        cockpitTintDelta: r1(S.tintDelta || 0),
        cockpit: {
          visible: !!K.visible, heightShare: r3((K.top + 1) / 2), wedges: K.wedges ? K.wedges.length : 0, wedgeAlpha: K.mats.wedge.opacity,
          glass: { opacity: r3(K.mats.glass.opacity), depthWrite: K.mats.glass.depthWrite, side: K.mats.glass.side === S.THREE.DoubleSide ? 'double' : 'single',
            last: K.glass.renderOrder === maxOrder, px: gp, value: S.set.glass },
          mfd: { text: K.mfdText, hz: 8 }, rise: r3(S.cockRise)
        },
        spill: { last: S.spill.kind, ms: Math.round(SPILL_T * 1000), suppressed: S.spill.suppressed, count: S.spill.count, on: S.spill.on },
        event: { count: S.evCount, uid: S.ev ? S.ev.uid : null },
        settings: { aspect: S.set.aspect, glass: S.set.glass, reduceFlashing: S.set.reduceFlashing, comfort: S.set.comfort, view: S.set.view }
      };
      S.trAudit = a.transition = {
        phase: S.tl ? S.transPhase : (tp === 'match' ? 'match' : null), t: r3(S.t), timeline: S.timeline.map(function (e) { return { name: e.name, from: e.from, to: e.to }; }),
        first: entries === 1, minClearance: isFinite(S.minClear) ? r3(S.minClear) : null, orbit: S.orbit, skips: S.skips,
        done: !!S.entered, planned: S.planned, sunLifts: S.sunLifts || 0, layer2d: S.layer ? r3(S.layer.op) : 0, layerPx: S.layer ? S.layer.px : null, glareK: r3(S.glareK), coronaK: r3(Math.max(S.coronaK || 0, S.glareK)), orbitSign: S.tl && S.tl.kind === 'enter' ? S.tl.orbitSign : (S.orbitSign || null),
        zoomCurve: 'log', exit: { samples: (S.exitSamples || []).slice(-120), ratios: logRatios(S.exitSamples || []) }, flyto: (S.flyDist || []).slice(-60),
        flashes: S.flashes
      };
      a.input = { held: { left: !!S.input.held.left, right: !!S.input.held.right, up: !!(S.input.up || S.input.held.up), down: !!(S.input.down || S.input.held.down), boost: !!S.input.held.boost, brake: !!S.input.held.brake },
        boostHeldMs: S.input.boostHeldMs, deck: !!(S.deck && S.deck.visible), gamepad: !!S.input.gamepad };
      a.input.flight = ctx.page && ctx.page.flightState ? ctx.page.flightState() : null;
      FH.rig = S.rig; FH.att = S.att; FH.t = S.t; FH.set = S.set; FH.transPhase = S.tl ? S.transPhase : null; FH.entered = S.entered; FH.viewRoll = S.viewRoll;
    },
    dispose: function () {
      if (!S) return;
      var K = S.cock;
      if (S.scene) S.scene.onAfterRender = K && K.prevAfter ? K.prevAfter : function () {};
      if (K) {
        K.meshes.forEach(function (m) { if (m.geometry) m.geometry.dispose(); });
        Object.keys(K.mats).forEach(function (k) { if (K.mats[k].map) K.mats[k].map.dispose(); K.mats[k].dispose(); });
        if (S.warp) { S.warp.lines.geometry.dispose(); S.warp.lines.material.dispose(); }
      }
      g.removeEventListener('keyup', S.onKeyUp); g.removeEventListener('blur', S.onBlur); g.document.removeEventListener('visibilitychange', S.onVis);
      g.removeEventListener('pointerdown', S.onPointer, true);
      deckDispose();
      clearTimeout(S.levelTimer);
      if (S.guide && S.guide.root.parentNode) S.guide.root.parentNode.removeChild(S.guide.root);
      layerDispose();
      if (S.ctxObj) Object.defineProperty(S.ctxObj, 'rig', { configurable: true, enumerable: true, writable: true, value: S.coreRig || 'chase' });
      S.flash(0); S.canvasFade(1);
      if (C.onKey === onKey) C.onKey = null;
      if (C.view3d === view3d) C.view3d = null;
      FH.hudAlpha = 0; FH.caption = null; FH.targetCard = null; FH.entry = null;
      S = null;
    }
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
